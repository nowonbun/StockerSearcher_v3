"""주가 DB와 분리된 MCP 감사 기록. 인증정보·응답 본문은 저장하지 않는다."""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
from time import perf_counter
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)
SEOUL = ZoneInfo('Asia/Seoul')
SAFE_ARGUMENTS = frozenset(('market', 'code', 'limit', 'start_date', 'end_date', 'date',
                            'weekly', 'mode', 'trans_amnt_min', 'close_max', 'tool'))
_WRITE_LOCK = threading.Lock()


def now_iso():
    return datetime.now(SEOUL).isoformat()


def request_metadata(scope):
    """User-Agent는 자기 신고 값이다. 전달 IP 헤더·쿠키·인증 헤더는 읽지 않는다."""
    headers = dict(scope.get('headers', []))
    client = headers.get(b'user-agent', b'unknown').decode('latin-1')[:200]
    address = scope.get('client')
    return {'client': client, 'ip': str(address[0])[:80] if address else ''}


class AuditStore:
    def __init__(self, path=None, retention_days=None):
        self.path = Path(path or os.getenv('MCP_AUDIT_DB', '/var/lib/stock-mcp/mcp_usage.sqlite3'))
        self.retention_days = int(retention_days if retention_days is not None else os.getenv('MCP_AUDIT_RETENTION_DAYS', '90'))
        if not 1 <= self.retention_days <= 3650:
            raise ValueError('MCP_AUDIT_RETENTION_DAYS must be between 1 and 3650')

    def record(self, *, kind, name, status, client='unknown', ip='', duration_ms=0,
               arguments=None, error_type=''):
        # allowlist의 스칼라만 기록한다. 전체 요청/응답·예외 메시지는 기록하지 않는다.
        safe = {key: (value[:100] if isinstance(value, str) else value)
                for key, value in (arguments or {}).items()
                if key in SAFE_ARGUMENTS and (value is None or type(value) in (str, int, float, bool))
                and (type(value) is not float or math.isfinite(value))}
        encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK, closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            # 스레드별 쓰기는 직렬화하고 다른 프로세스와는 트랜잭션 시작부터 잠금을 협상한다.
            conn.execute('BEGIN IMMEDIATE')
            # rollback journal은 읽기 전용 볼륨에서도 WAL 보조 파일 생성 없이 조회 가능하다.
            conn.execute('''CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, kind TEXT NOT NULL,
                name TEXT NOT NULL, status TEXT NOT NULL, client TEXT NOT NULL, ip TEXT NOT NULL,
                duration_ms REAL NOT NULL, arguments TEXT NOT NULL, error_type TEXT NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS events_date ON events(occurred_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS events_kind_name ON events(kind, name)')
            conn.execute('INSERT INTO events(occurred_at,kind,name,status,client,ip,duration_ms,arguments,error_type) VALUES (?,?,?,?,?,?,?,?,?)',
                         (now_iso(), kind, str(name)[:100], str(status)[:30], str(client)[:200], str(ip)[:80],
                          round(duration_ms, 2), encoded, str(error_type)[:80]))
            cutoff = (datetime.now(SEOUL) - timedelta(days=self.retention_days)).isoformat()
            conn.execute('DELETE FROM events WHERE occurred_at < ?', (cutoff,))

    def report(self, filters):
        clauses, values = [], []
        kind = filters.get('kind', '')
        if kind not in ('', 'access', 'tool'):
            raise ValueError('기록 유형을 확인하세요.')
        try:
            page = int(filters.get('page_number', filters.get('page', '1')))
            if not 1 <= page <= 100000:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('페이지는 1~100000 사이의 정수여야 합니다.') from None
        for field in ('start', 'end'):
            value = filters.get(field, '')
            if value:
                try:
                    day = datetime.strptime(value, '%Y-%m-%d').date()
                    if day.isoformat() != value:
                        raise ValueError
                except (TypeError, ValueError, OverflowError):
                    raise ValueError('기간은 유효한 YYYY-MM-DD여야 합니다.') from None
                if field == 'end' and day.year == 9999 and day.month == 12 and day.day == 31:
                    raise ValueError('종료일은 9999-12-31보다 빨라야 합니다.')
                clauses.append('occurred_at >= ?' if field == 'start' else 'occurred_at < ?')
                values.append(value if field == 'start' else (day + timedelta(days=1)).isoformat())
        if filters.get('start') and filters.get('end') and filters['start'] > filters['end']:
            raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
        for key, column in (('kind', 'kind'), ('tool', 'name'), ('status', 'status'), ('client', 'client')):
            if filters.get(key):
                clauses.append(f'{column} = ?')
                values.append(str(filters[key])[:200])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        result = {'available': False, 'retention_days': self.retention_days, 'page': page,
                  'total': 0, 'counts': {'access': 0, 'tools': 0, 'errors': 0},
                  'rows': [], 'daily': [], 'tools': [], 'clients': []}
        if not self.path.exists():
            return result
        # 조회에서는 DB/테이블/저널을 생성하거나 수정하지 않는다.
        uri = self.path.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN')  # 집계와 상세 목록은 같은 읽기 스냅샷을 사용한다.
            def rows(sql, params=values):
                return [dict(row) for row in conn.execute(sql, params)]
            result['available'] = True
            result['counts'] = rows('''SELECT
                COALESCE(SUM(kind='access'),0) AS access,
                COALESCE(SUM(kind='tool'),0) AS tools,
                COALESCE(SUM(kind='tool' AND status IN ('failed','cancelled')),0) AS errors
                FROM events''' + where)[0]
            result['total'] = conn.execute('SELECT COUNT(*) FROM events' + where, values).fetchone()[0]
            result['rows'] = rows('SELECT * FROM events' + where + ' ORDER BY id DESC LIMIT 50 OFFSET ?', [*values, (page-1)*50])
            result['daily'] = rows('SELECT substr(occurred_at,1,10) AS day, COUNT(*) AS count FROM events' + where + ' GROUP BY day ORDER BY day DESC LIMIT 90')
            tool_where = where + (' AND ' if where else ' WHERE ') + "kind='tool'"
            result['tools'] = rows('SELECT name, COUNT(*) AS count FROM events' + tool_where + ' GROUP BY name ORDER BY count DESC LIMIT 30')
            result['clients'] = rows('SELECT client, COUNT(*) AS count FROM events' + where + ' GROUP BY client ORDER BY count DESC LIMIT 30')
        return result


def safe_record(**event):
    try:
        AuditStore().record(**event)
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        # 저장 실패는 운영 로그에 명시하되 주가 조회 실패로 전파하지 않는다.
        LOG.error('MCP 감사 기록 저장 실패 (%s); MCP_AUDIT_DB 경로·권한·용량을 확인하세요.', type(error).__name__)


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path', '').rstrip('/') != '/mcp':
            return await self.app(scope, receive, send)
        start, status = perf_counter(), '500'
        async def observed_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = str(message['status'])
            await send(message)
        try:
            await self.app(scope, receive, observed_send)
        finally:
            await asyncio.to_thread(safe_record, kind='access', name=scope.get('method', ''),
                                    status=status, duration_ms=(perf_counter()-start)*1000,
                                    **request_metadata(scope))
