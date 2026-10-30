"""자체 배치 큐·예약·관리 API. Linux 컨테이너에서 단일 worker로 실행합니다."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from batch.config import BatchConfig, MARKETS, MODES, saved_mode
from batch.history import HistoryStore
from batch.runner import MarketLock, _environment
from batch.scheduler import Cron
from batch.scheduler import SEOUL

LOG = logging.getLogger(__name__)
ACTIVE = ('queued', 'running', 'cancelling')


class Conflict(ValueError):
    pass


class BatchManager:
    def __init__(self, config: BatchConfig):
        self.config = config
        config.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = config.state_dir / 'manager.sqlite3'
        self.lock = threading.RLock()
        self.active = None
        self.history = HistoryStore(config.database_path)
        with self.db() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    run_id TEXT PRIMARY KEY, market TEXT, mode TEXT, trigger_source TEXT,
                    status TEXT, created_at TEXT, started_at TEXT, finished_at TEXT, error_message TEXT);
                CREATE TABLE IF NOT EXISTS schedules (
                    market TEXT PRIMARY KEY, enabled INTEGER, cron TEXT, mode TEXT);
                CREATE TABLE IF NOT EXISTS fires (
                    market TEXT, minute TEXT, PRIMARY KEY(market, minute));
            ''')
            for market, cron in [('JP', '0 12,18 * * 1-5'), ('KR', '0 14,20 * * 1-5')]:
                expression = os.environ.get(f'BATCH_SCHEDULE_{market}', cron)
                Cron(expression)
                mode = saved_mode(os.environ.get('BATCH_SCHEDULE_MODE', 'collect'))
                self.validate(market, mode)
                conn.execute('INSERT OR IGNORE INTO schedules VALUES (?, 1, ?, ?)', (market, expression, mode))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def validate(market, mode):
        if market not in MARKETS or mode not in MODES:
            raise ValueError('market 또는 mode가 허용 목록에 없습니다')

    def _update(self, run_id, **values):
        allowed = {'status', 'started_at', 'finished_at', 'error_message'}
        if not values or not values.keys() <= allowed:
            raise ValueError('허용되지 않은 상태 변경')
        with self.db() as conn:
            conn.execute('UPDATE jobs SET ' + ', '.join(f'{key}=?' for key in values) + ' WHERE run_id=?', (*values.values(), run_id))

    def _finish_history(self, run_id, status, error=None):
        now = datetime.now(UTC).isoformat()
        conn = self.history._connection()
        try:
            with conn:
                conn.execute("UPDATE batch_tasks SET status=?, finished_at=?, exit_code=-1 WHERE run_id=? AND status='running'", (status, now, run_id))
                conn.execute("UPDATE batch_runs SET status=?, finished_at=?, error_message=? WHERE run_id=? AND status='running'", (status, now, error, run_id))
        finally:
            conn.close()

    def recover(self):
        # 소유권 잠금을 획득한 서비스만 호출합니다. 오래된 PID를 읽어 kill하지 않습니다.
        with self.lock, self.db() as conn:
            interrupted = conn.execute("SELECT run_id FROM jobs WHERE status IN ('running','cancelling')").fetchall()
            for row in interrupted:
                self._finish_history(row['run_id'], 'interrupted', '배치 서비스 재시작')
            conn.execute("UPDATE jobs SET status='interrupted', finished_at=?, error_message='배치 서비스 재시작' WHERE status IN ('running','cancelling')", (datetime.now(UTC).isoformat(),))
            # 단일 서비스 잠금 아래에서만 전환합니다. 완료 이력의 모드는 유지합니다.
            for schedule in conn.execute('SELECT market, mode FROM schedules').fetchall():
                mode = saved_mode(schedule['mode'])
                if mode != schedule['mode']:
                    conn.execute('UPDATE schedules SET mode=? WHERE market=?', (mode, schedule['market']))
                    LOG.warning('기존 예약 패턴 전환: %s %s -> %s', schedule['market'], schedule['mode'], mode)
            seen = set()
            for job in conn.execute("SELECT run_id, market, mode FROM jobs WHERE status='queued' ORDER BY created_at, run_id").fetchall():
                mode = saved_mode(job['mode'])
                key = (job['market'], mode)
                if key in seen:
                    conn.execute("UPDATE jobs SET status='cancelled', finished_at=?, error_message='세 패턴 통합으로 동일 시장·작업 대기열 중복 취소' WHERE run_id=?", (datetime.now(UTC).isoformat(), job['run_id']))
                else:
                    seen.add(key)
                    conn.execute('UPDATE jobs SET mode=? WHERE run_id=?', (mode, job['run_id']))

    def submit(self, market, mode, source='web'):
        self.validate(market, mode)
        with self.lock, self.db() as conn:
            count = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
            if count >= 100:
                raise Conflict('대기열 한도 100개 초과')
            if conn.execute("SELECT 1 FROM jobs WHERE market=? AND mode=? AND status IN ('queued','running','cancelling')", (market, mode)).fetchone():
                raise Conflict('동일 시장·모드가 대기 또는 실행 중입니다')
            run_id = uuid.uuid4().hex
            conn.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)', (run_id, market, mode, source, 'queued', datetime.now(UTC).isoformat()))
        return self.detail(run_id)

    def runs(self):
        with self.db() as conn:
            jobs = [dict(row) for row in conn.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100')]
        ids = {row['run_id'] for row in jobs}
        for row in self.history.recent_runs(100):
            if row['run_id'] not in ids:
                row.pop('log_path', None)
                row['created_at'] = row['started_at']
                row['legacy'] = True
                jobs.append(row)
        return sorted(jobs, key=lambda row: row['created_at'], reverse=True)[:100]

    def summary(self, now=None):
        """최근 목록의 100건 제한과 별도로 전체 이력을 집계하며 중복 ID를 제외합니다."""
        today = (now or datetime.now(UTC)).astimezone(SEOUL).date()
        dates = [(today - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
        union = '''WITH all_runs AS (
            SELECT run_id, status, COALESCE(finished_at, started_at, created_at) AS event_at FROM jobs
            UNION ALL
            SELECT h.run_id, h.status, COALESCE(h.finished_at, h.started_at) FROM history.batch_runs h
            WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.run_id=h.run_id)
        ) '''
        with self.db() as conn:
            conn.execute('ATTACH DATABASE ? AS history', (str(self.config.database_path),))
            counts = conn.execute(union + '''SELECT COUNT(*) total_runs,
                SUM(status IN ('running','cancelling')) running, SUM(status='queued') queued,
                SUM(status='succeeded' AND date(event_at, '+9 hours')=?) today_succeeded,
                SUM(status='failed' AND date(event_at, '+9 hours')=?) today_failed FROM all_runs''', (today.isoformat(), today.isoformat())).fetchone()
            rows = conn.execute(union + '''SELECT date(event_at, '+9 hours') day,
                SUM(status='succeeded') succeeded, SUM(status='failed') failed,
                SUM(status NOT IN ('succeeded','failed')) running FROM all_runs
                WHERE date(event_at, '+9 hours') BETWEEN ? AND ? GROUP BY day''', (dates[0], dates[-1])).fetchall()
        daily = {row['day']: dict(row) for row in rows}
        return {**{key: counts[key] or 0 for key in counts.keys()}, 'registered_jobs': len(MARKETS) * len(MODES),
                'daily': [{'date': day, **{key: daily.get(day, {}).get(key, 0) for key in ('succeeded', 'failed', 'running')}} for day in dates]}

    def detail(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise KeyError('실행 ID를 찾을 수 없습니다')
        with self.db() as conn:
            row = conn.execute('SELECT * FROM jobs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            legacy = next((r for r in self.history.recent_runs(100) if r['run_id'] == run_id), None)
            if legacy is None:
                raise KeyError('실행 ID를 찾을 수 없습니다')
            legacy.pop('log_path', None)
            row = legacy
        result = dict(row)
        conn = self.history._connection()
        try:
            result['tasks'] = [dict(task) for task in conn.execute('SELECT task_name, status, started_at, finished_at, exit_code FROM batch_tasks WHERE run_id=? ORDER BY started_at', (run_id,))]
        finally:
            conn.close()
        return result

    def log(self, run_id):
        job = self.detail(run_id)
        path = self.config.log_dir / f"{run_id}_{job['market'].lower()}_{job['mode']}.log"
        root = self.config.log_dir.resolve()
        if not path.resolve().is_relative_to(root):
            raise ValueError('로그 경로가 허용 범위를 벗어났습니다')
        if not path.exists():
            path = self.config.log_dir / f'{run_id}.manager.log'
        if not path.resolve().is_relative_to(root):
            raise ValueError('로그 경로가 허용 범위를 벗어났습니다')
        text = ''
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                text = stream.read(65536).decode('utf-8', errors='replace')
        return {'text': text, 'tail_bytes': 65536}

    def cancel(self, run_id):
        with self.lock:
            job = self.detail(run_id)
            if job['status'] == 'queued':
                self._update(run_id, status='cancelled', finished_at=datetime.now(UTC).isoformat())
            elif self.active and self.active[0] == run_id and job['status'] == 'running':
                self.active[1].terminate()  # wrapper가 하위 프로세스 그룹을 종료합니다.
                self._update(run_id, status='cancelling')
            else:
                raise Conflict('현재 서비스가 소유한 취소 가능한 실행이 아닙니다')
        return self.detail(run_id)

    def schedules(self):
        with self.db() as conn:
            return [{**dict(row), 'enabled': bool(row['enabled']), 'timezone': 'Asia/Seoul'} for row in conn.execute('SELECT * FROM schedules ORDER BY market')]

    def update_schedule(self, market, body):
        if not isinstance(body, dict) or set(body) != {'enabled', 'cron', 'mode'} or type(body['enabled']) is not bool:
            raise ValueError('enabled(bool), cron, mode가 필요합니다')
        self.validate(market, body['mode'])
        Cron(body['cron'])
        with self.lock, self.db() as conn:
            conn.execute('UPDATE schedules SET enabled=?, cron=?, mode=? WHERE market=?', (body['enabled'], body['cron'], body['mode'], market))
        return self.schedules()

    def _launch(self, job):
        run_id = job['run_id']
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        output = (self.config.log_dir / f'{run_id}.manager.log').open('ab')
        try:
            command = [sys.executable, '-m', 'batch.process_wrapper', '--pgid-file', str(self.config.lock_dir / f'{run_id}.service.pgid'), '--', sys.executable, '-m', 'batch.cli', 'run', '--market', job['market'].lower(), '--mode', job['mode'], '--trigger-source', job['trigger_source'], '--run-id', run_id]
            environment = _environment(self.config)
            environment.update(BATCH_STATE_DIR=str(self.config.state_dir), BATCH_LOG_DIR=str(self.config.log_dir), BATCH_SOURCE_DIR=str(self.config.source_dir), BATCH_TASK_TIMEOUT_SECONDS=str(self.config.subprocess_timeout_seconds))
            process = subprocess.Popen(command, cwd=self.config.source_dir, env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        except Exception:
            output.close()
            raise
        self.active = (run_id, process, output)
        self._update(run_id, status='running', started_at=datetime.now(UTC).isoformat())

    def tick(self, now=None):
        now = now or datetime.now(UTC)
        with self.lock:
            if self.active:
                run_id, process, output = self.active
                code = process.poll()
                if code is not None:
                    process.wait()
                    output.close()
                    status = 'cancelled' if self.detail(run_id)['status'] == 'cancelling' else ('succeeded' if code == 0 else 'failed')
                    error = None if status == 'succeeded' else f'프로세스 종료 코드: {code}'
                    self._update(run_id, status=status, finished_at=now.isoformat(), error_message=error)
                    self._finish_history(run_id, status, error)
                    self.active = None
            minute = now.astimezone(UTC).replace(second=0, microsecond=0).isoformat()
            for schedule in self.schedules():
                if not schedule['enabled'] or not Cron(schedule['cron']).matches(now):
                    continue
                with self.db() as conn:
                    inserted = conn.execute('INSERT OR IGNORE INTO fires VALUES (?, ?)', (schedule['market'], minute)).rowcount
                if inserted:
                    # 과거 예약은 재생하지 않습니다. 현재 분 예약만 판정합니다.
                    if now.second > int(os.environ.get('BATCH_MAX_SCHEDULE_DELAY_SECONDS', '18000')):
                        continue
                    try:
                        self.submit(schedule['market'], schedule['mode'], 'schedule')
                    except Conflict:
                        LOG.warning('예약 중복 또는 큐 한도: %s', schedule['market'])
            if self.active is None:
                with self.db() as conn:
                    job = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if job:
                    try:
                        self._launch(dict(job))
                    except OSError as error:
                        self._update(job['run_id'], status='failed', finished_at=now.isoformat(), error_message=str(error))

    def close(self):
        with self.lock:
            if self.active:
                run_id, process, output = self.active
                self.cancel(run_id) if self.detail(run_id)['status'] == 'running' else None
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                output.close()
                self.active = None
                self._update(run_id, status='cancelled', finished_at=datetime.now(UTC).isoformat())
                self._finish_history(run_id, 'cancelled', '배치 서비스 종료')


def create_app(manager: BatchManager):

    async def endpoint(request: Request):
        # 브라우저에서 내부 API로 직접 보내는 교차 출처 쓰기는 허용하지 않습니다.
        if request.method != 'GET' and (request.headers.get('origin') or request.headers.get('sec-fetch-site') == 'cross-site'):
            return JSONResponse({'error': '다른 출처의 쓰기 요청은 허용하지 않습니다'}, status_code=403)
        try:
            action = request.url.path.strip('/').split('/')
            if request.method == 'GET':
                if action == ['summary']:
                    result = manager.summary()
                elif action == ['runs']:
                    result = {'runs': manager.runs(), 'modes': list(MODES)}
                elif action == ['schedules']:
                    result = {'schedules': manager.schedules()}
                elif len(action) == 2 and action[0] == 'runs':
                    result = manager.detail(action[1])
                elif len(action) == 3 and action[0] == 'runs' and action[2] == 'log':
                    result = manager.log(action[1])
                else:
                    raise KeyError('경로 없음')
                return JSONResponse(result)
            if action == ['runs'] and request.method == 'POST':
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 4096:
                        raise ValueError('요청 본문은 4096바이트 이하여야 합니다')
                body = json.loads(raw)
                if not isinstance(body, dict) or set(body) != {'market', 'mode'}:
                    raise ValueError('market, mode만 전달하세요')
                return JSONResponse(manager.submit(body['market'], body['mode']), status_code=202)
            if len(action) == 3 and action[0] == 'runs' and action[2] == 'cancel' and request.method == 'POST':
                return JSONResponse(manager.cancel(action[1]))
            if len(action) == 2 and action[0] == 'schedules' and request.method == 'PUT':
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 4096:
                        raise ValueError('요청 본문 초과')
                return JSONResponse({'schedules': manager.update_schedule(action[1], json.loads(raw))})
            raise KeyError('경로 없음')
        except Conflict as error:
            return JSONResponse({'error': str(error)}, status_code=409)
        except (ValueError, TypeError) as error:
            return JSONResponse({'error': str(error)}, status_code=400)
        except KeyError:
            return JSONResponse({'error': '실행 또는 경로를 찾을 수 없습니다'}, status_code=404)

    @asynccontextmanager
    async def lifespan(app):
        with MarketLock(manager.config.lock_dir / 'service.lock'):
            manager.recover()
            stop = asyncio.Event()

            async def loop():
                while not stop.is_set():
                    try:
                        manager.tick()
                    except Exception:
                        LOG.exception('배치 tick 실패')
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=1)
                    except TimeoutError:
                        pass

            task = asyncio.create_task(loop())
            try:
                yield
            finally:
                stop.set()
                await task
                await asyncio.to_thread(manager.close)

    return Starlette(lifespan=lifespan, routes=[
        Route('/health', lambda request: JSONResponse({'status': 'ok'})),
        Route('/summary', endpoint),
        Route('/runs', endpoint, methods=['GET', 'POST']),
        Route('/runs/{run_id}', endpoint),
        Route('/runs/{run_id}/log', endpoint),
        Route('/runs/{run_id}/cancel', endpoint, methods=['POST']),
        Route('/schedules', endpoint),
        Route('/schedules/{market}', endpoint, methods=['PUT']),
    ])


def main():
    import uvicorn
    if sys.platform != 'linux':
        raise RuntimeError('배치 서비스는 Linux 컨테이너에서 실행하세요')
    manager = BatchManager(BatchConfig.from_environment())
    uvicorn.run(create_app(manager), host='0.0.0.0', port=8001)


if __name__ == '__main__':
    main()
