from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from batch.config import BatchConfig
from batch.scheduler import Cron
from batch.service import BatchManager, create_app
from starlette.requests import Request


class SchedulerTests(unittest.TestCase):
    def test_compose_replaces_prefect_with_internal_batch_service(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        for filename in ('docker-compose.yaml', 'docker-compose.server.yml'):
            text = (root / filename).read_text(encoding='utf-8')
            compose = yaml.safe_load(text)
            self.assertNotIn('prefect-server', compose['services'])
            self.assertNotIn('prefect-runner', compose['services'])
            self.assertEqual(compose['services']['batch']['command'], 'python -m batch.service')
            self.assertNotIn('ports', compose['services']['batch'])
            self.assertNotIn('BATCH_ADMIN_TOKEN', compose['services']['batch']['environment'])
            self.assertNotIn('prefect', text.lower())

    def test_weekday_schedule_uses_seoul_time(self):
        cron = Cron('0 12,18 * * 1-5')
        self.assertTrue(cron.matches(datetime(2026, 10, 29, 3, tzinfo=timezone.utc)))
        self.assertFalse(cron.matches(datetime(2026, 10, 31, 3, tzinfo=timezone.utc)))

    def test_lists_steps_and_sunday(self):
        self.assertTrue(Cron('*/15 9-18 * * 0,7').matches(datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc)))
        for value in ('bad', '60 * * * *', '* * * * 8', '*/0 * * * *', '* * 0 * *', '1,,2 * * * *'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Cron(value)


class BatchServiceTests(unittest.TestCase):
    def test_dashboard_summary_is_exact_across_history_and_seoul_midnight(self):
        job = self.manager.submit('JP', 'collect')
        self.manager._update(job['run_id'], status='succeeded', started_at='2026-10-28T14:59:00+00:00', finished_at='2026-10-28T15:01:00+00:00')
        self.manager.history.start_run(run_id=job['run_id'], market='JP', mode='collect', trigger_source='web', started_at='2026-10-28T14:59:00+00:00', log_path='unused')
        self.manager.history.finish_run(job['run_id'], '2026-10-28T15:01:00+00:00', 'succeeded')
        self.manager.history.start_run(run_id='b'*32, market='KR', mode='collect', trigger_source='cli', started_at='2026-10-28T15:02:00+00:00', log_path='unused')
        self.manager.history.finish_run('b'*32, '2026-10-28T15:03:00+00:00', 'failed')
        summary = self.manager.summary(datetime(2026, 10, 28, 16, tzinfo=timezone.utc))
        self.assertEqual(summary['today_succeeded'], 1)
        self.assertEqual(summary['today_failed'], 1)
        self.assertEqual(summary['total_runs'], 2)
        self.assertEqual(summary['daily'][-1]['date'], '2026-10-29')
        self.assertEqual(len(summary['daily']), 7)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = BatchConfig(self.root, self.root / 'state', self.root / 'logs', 10)
        self.manager = BatchManager(self.config)
        for schedule in self.manager.schedules():
            self.manager.update_schedule(schedule['market'], {'enabled': False, 'cron': schedule['cron'], 'mode': schedule['mode']})

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_queue_validation_persistence_and_cancel(self):
        with self.assertRaises(ValueError):
            self.manager.submit('US', 'collect')
        with self.assertRaises(ValueError):
            self.manager.submit('KR', 'shell-command')
        job = self.manager.submit('KR', 'collect')
        with self.assertRaises(ValueError):
            self.manager.submit('KR', 'collect')
        self.assertEqual(BatchManager(self.config).runs()[0]['run_id'], job['run_id'])
        self.manager.cancel(job['run_id'])
        self.assertEqual(self.manager.detail(job['run_id'])['status'], 'cancelled')
        with self.assertRaises(KeyError):
            self.manager.detail('../outside')

    def test_scheduler_deduplicates_and_persists_settings(self):
        self.manager.update_schedule('JP', {'enabled': True, 'cron': '0 12 * * 1-5', 'mode': 'collect'})
        now = datetime(2026, 10, 29, 3, tzinfo=timezone.utc)
        with patch.object(self.manager, '_launch'):
            self.manager.tick(now)
            self.manager.tick(now)
        self.assertEqual(len(self.manager.runs()), 1)
        self.assertEqual(BatchManager(self.config).schedules()[0]['mode'], 'collect')
        with self.assertRaises(ValueError):
            self.manager.update_schedule('JP', {'enabled': 'false', 'cron': '* * * * *', 'mode': 'collect'})

    def test_single_worker_completion_and_running_cancel(self):
        job = self.manager.submit('JP', 'collect')
        self.manager.submit('KR', 'collect')
        process = Mock()
        process.poll.return_value = None
        with patch('batch.service.subprocess.Popen', return_value=process) as popen:
            self.manager.tick()
            self.manager.tick()
            self.assertEqual(popen.call_count, 1)
            command = popen.call_args.args[0]
            self.assertIn('batch.process_wrapper', command)
            self.assertIn('--run-id', command)
            self.assertNotIn('shell', popen.call_args.kwargs)
            self.manager.cancel(job['run_id'])
            process.terminate.assert_called_once()
            process.poll.return_value = 130
            self.manager.tick()
        self.assertEqual(self.manager.detail(job['run_id'])['status'], 'cancelled')

    def test_start_failure_does_not_stall_queue(self):
        job = self.manager.submit('JP', 'collect')
        with patch('batch.service.subprocess.Popen', side_effect=OSError('spawn failed')):
            self.manager.tick()
        self.assertEqual(self.manager.detail(job['run_id'])['status'], 'failed')

    def test_restart_marks_interrupted_jobs_and_tasks(self):
        job = self.manager.submit('JP', 'collect')
        self.manager._update(job['run_id'], status='running')
        self.manager.history.start_run(run_id=job['run_id'], market='JP', mode='collect', trigger_source='web', started_at='2026-10-29T00:00:00+00:00', log_path='unused')
        self.manager.history.start_task(job['run_id'], 'collect', '2026-10-29T00:00:00+00:00', 'test')
        self.manager.recover()
        self.assertEqual(self.manager.detail(job['run_id'])['status'], 'interrupted')
        self.assertEqual(self.manager.detail(job['run_id'])['tasks'][0]['status'], 'interrupted')

    def test_successful_completion_starts_next_job(self):
        first = self.manager.submit('JP', 'collect')
        second = self.manager.submit('KR', 'collect')
        process = Mock()
        process.poll.return_value = None
        with patch('batch.service.subprocess.Popen', return_value=process) as popen:
            self.manager.tick()
            process.poll.return_value = 0
            self.manager.tick()
            self.assertEqual(self.manager.detail(first['run_id'])['status'], 'succeeded')
            self.assertEqual(self.manager.detail(second['run_id'])['status'], 'running')
            self.assertEqual(popen.call_count, 2)
        self.manager.close()

    def test_disabled_schedule_and_missed_minutes_are_not_replayed(self):
        self.manager.update_schedule('JP', {'enabled': False, 'cron': '0 12 * * *', 'mode': 'collect'})
        self.manager.update_schedule('KR', {'enabled': True, 'cron': '0 12 * * *', 'mode': 'collect'})
        with patch.object(self.manager, '_launch'):
            self.manager.tick(datetime(2026, 10, 29, 3, 1, tzinfo=timezone.utc))
            self.assertEqual(self.manager.runs(), [])
            self.manager.tick(datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc))
        self.assertEqual(len(self.manager.runs()), 1)
        self.assertEqual(self.manager.runs()[0]['market'], 'KR')

    def test_log_symlink_cannot_escape_log_directory(self):
        job = self.manager.submit('JP', 'collect')
        self.config.log_dir.mkdir(parents=True)
        path = self.config.log_dir / f"{job['run_id']}_jp_collect.log"
        outside = self.root / 'outside.log'
        outside.write_text('not allowed', encoding='utf-8')
        with patch.object(Path, 'resolve', autospec=True) as resolve:
            resolve.side_effect = lambda value: outside if value == path else value
            with self.assertRaises(ValueError):
                self.manager.log(job['run_id'])

    def test_log_is_bounded_and_paths_are_confined(self):
        job = self.manager.submit('JP', 'collect')
        path = self.config.log_dir / f"{job['run_id']}_jp_collect.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('한글 로그\n' * 20000, encoding='utf-8')
        result = self.manager.log(job['run_id'])
        self.assertLessEqual(len(result['text'].encode('utf-8')), 65540)
        self.assertNotIn('log_path', self.manager.detail(job['run_id']))


class BatchApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_submission_keeps_validation_and_origin_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = BatchManager(BatchConfig(root, root / 'state', root / 'logs', 10))
            app = create_app(manager)
            route = next(route for route in app.routes if route.path == '/runs')

            async def request(body, origin=None):
                async def receive():
                    return {'type': 'http.request', 'body': json.dumps(body).encode()}
                headers = [(b'origin', origin.encode())] if origin else []
                return await route.endpoint(Request({'type': 'http', 'method': 'POST', 'path': '/runs', 'headers': headers}, receive))

            self.assertEqual((await request({'market': 'KR', 'mode': 'collect'}, 'https://evil.example')).status_code, 403)
            self.assertEqual((await request({'market': 'KR', 'mode': 'arbitrary'})).status_code, 400)
            self.assertEqual((await request({'market': 'KR', 'mode': 'collect'})).status_code, 202)
            self.assertEqual((await request({'market': 'KR', 'mode': 'collect'})).status_code, 409)
            manager.close()


if __name__ == '__main__':
    unittest.main()
