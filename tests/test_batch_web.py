from __future__ import annotations

import unittest
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from starlette.requests import Request

from batch.web import create_web_app, dashboard_html


class PythonBatchWebTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Mock()
        self.client.get.side_effect = lambda path: {
            '/runs': {'runs': [], 'modes': ['collect', 'predict', 'full']},
            '/schedules': {'schedules': [{'market': 'JP', 'enabled': True, 'cron': '0 12 * * 1-5', 'mode': 'collect'}]},
            '/summary': {'registered_jobs': 6, 'running': 0, 'queued': 0, 'today_succeeded': 0, 'today_failed': 0, 'daily': []},
        }[path]
        self.app = create_web_app(client=self.client)

    async def call(self, path='/', method='GET', body=None, cookie='', query='', extra_headers=None):
        route = next(r for r in self.app.routes if r.path == '/{path:path}')
        payload = urlencode(body or {}).encode()
        async def receive():
            return {'type': 'http.request', 'body': payload}
        headers = [(b'host', b'localhost'), (b'content-type', b'application/x-www-form-urlencoded'), (b'cookie', cookie.encode())]
        headers.extend(extra_headers or [])
        request = Request({'type': 'http', 'scheme': 'http', 'server': ('localhost', 80), 'client': ('127.0.0.1', 1234), 'method': method, 'path': path, 'query_string': query.encode(), 'headers': headers}, receive)
        return await route.endpoint(request)

    async def csrf_cookie(self):
        response = await self.call('/')
        self.assertEqual(response.status_code, 200)
        cookies = SimpleCookie(); cookies.load(response.headers['set-cookie'])
        return f"batch_csrf={cookies['batch_csrf'].value}"

    async def test_dashboard_opens_without_login_and_keeps_security_headers(self):
        self.assertEqual((await self.call()).status_code, 200)
        cookie = await self.csrf_cookie()
        response = await self.call(cookie=cookie)
        text = response.body.decode()
        self.assertEqual(response.status_code, 200)
        for label in ('대시보드', '배치 작업 관리', '스케줄 관리', '실행 이력', '로그 조회', '시스템 설정', '최근 7일 실행 현황', '빠른 실행'):
            self.assertIn(label, text)
        self.assertNotIn('type="password"', text)
        self.assertNotIn('/logout', text)
        self.assertNotIn('<script', text)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])

    async def test_csrf_required_before_any_mutation_and_external_origin_rejected(self):
        cookie = await self.csrf_cookie()
        response = await self.call('/run', 'POST', {'market': 'JP', 'mode': 'collect'}, cookie)
        self.assertEqual(response.status_code, 403)
        self.client.post.assert_not_called()
        response = await self.call('/run', 'POST', {}, cookie, extra_headers=[(b'origin', b'https://evil.example')])
        self.assertEqual(response.status_code, 403)

    async def test_form_submission_and_removed_login_routes(self):
        import re
        cookie = await self.csrf_cookie()
        page = await self.call(cookie=cookie, query='page=jobs')
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.body.decode()).group(1)
        self.client.post.return_value = {'run_id': 'a' * 32}
        response = await self.call('/run', 'POST', {'csrf': csrf, 'market': 'JP', 'mode': 'collect'}, cookie)
        self.assertEqual(response.status_code, 303)
        self.client.post.assert_called_once_with('/runs', {'market': 'JP', 'mode': 'collect'})
        self.assertEqual((await self.call('/logout', 'POST', {'csrf': csrf}, cookie)).status_code, 404)
        self.assertEqual((await self.call(cookie=cookie)).status_code, 200)

    async def test_schedule_cancel_and_invalid_execution_use_allowlisted_api(self):
        import re
        cookie = await self.csrf_cookie()
        page = await self.call(cookie=cookie)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.body.decode()).group(1)
        response = await self.call('/schedule/JP', 'POST', {'csrf': csrf, 'cron': '0 12 * * 1-5', 'mode': 'collect', 'enabled': '1'}, cookie)
        self.assertEqual(response.status_code, 303)
        self.client.put.assert_called_once_with('/schedules/JP', {'cron':'0 12 * * 1-5', 'mode':'collect', 'enabled': True})
        response = await self.call('/cancel/'+'a'*32, 'POST', {'csrf':csrf}, cookie)
        self.assertEqual(response.status_code, 303)
        self.client.post.assert_called_once_with('/runs/'+'a'*32+'/cancel')
        self.client.post.reset_mock()
        response = await self.call('/run', 'POST', {'csrf':csrf, 'market':'JP', 'mode':'rm -rf /'}, cookie)
        self.assertEqual(response.status_code, 400)
        self.client.post.assert_not_called()

    async def test_csrf_cookie_survives_restart_without_login_session(self):
        import re
        cookie = await self.csrf_cookie()
        response = await self.call(cookie=cookie)
        csrf = re.search(r'name="csrf" value="([^"]+)"', response.body.decode()).group(1)
        self.app = create_web_app(client=self.client)
        self.client.post.return_value = {'run_id': 'a' * 32}
        response = await self.call('/run', 'POST', {'csrf':csrf, 'market':'JP', 'mode':'collect'}, cookie)
        self.assertEqual(response.status_code, 303)
        response = await self.call()
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])
        self.assertNotIn('batch_session', response.headers['set-cookie'])

    async def test_duplicate_form_fields_are_rejected(self):
        cookie = await self.csrf_cookie()
        response = await self.call('/run', 'POST', [('mode','collect'),('mode','collect')], cookie)
        self.assertEqual(response.status_code, 400)
        self.client.post.assert_not_called()

    async def test_removed_login_route_does_not_reflect_untrusted_input(self):
        response = await self.call('/login', 'POST', {'unexpected': '<script>bad</script>'})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('<script>bad</script>', response.body.decode())

    async def test_python_css_is_public_and_no_frontend_build_required(self):
        response = await self.call('/assets/dashboard.css')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'.sidebar', response.body)
        self.assertIn('text/css', response.headers['content-type'])
        self.assertEqual((await self.call('/assets/../service.py')).status_code, 404)

    async def test_bad_inputs_and_backend_errors_are_visible_without_tracebacks(self):
        from batch.web import BackendError
        cookie = await self.csrf_cookie()
        self.assertEqual((await self.call(cookie=cookie, query='page=unknown')).status_code, 404)
        self.client.get.side_effect = BackendError(503, '배치 API 연결 실패')
        response = await self.call(cookie=cookie)
        self.assertEqual(response.status_code, 503)
        self.assertIn('배치 API 연결 실패', response.body.decode())
        self.assertNotIn('Traceback', response.body.decode())


class DashboardRenderTests(unittest.TestCase):
    def test_manual_execution_is_one_form_with_only_three_patterns(self):
        from html.parser import HTMLParser
        from batch.config import MODES, MARKETS

        class FormParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms = []; self.current = None; self.select = None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'form' and attrs.get('action') == '/run':
                    self.current = {'selects': {}, 'buttons': 0, 'csrf': False, 'groups': 0}
                    self.forms.append(self.current)
                if self.current is None:
                    return
                if tag == 'select':
                    self.select = attrs['name']
                    self.current['selects'][self.select] = {'required': 'required' in attrs, 'values': []}
                if tag == 'option' and self.select:
                    self.current['selects'][self.select]['values'].append(attrs.get('value'))
                if tag == 'optgroup': self.current['groups'] += 1
                if tag == 'button': self.current['buttons'] += 1
                if tag == 'input' and attrs.get('name') == 'csrf':
                    self.current['csrf'] = attrs.get('value') == 'safe'
            def handle_endtag(self, tag):
                if tag == 'select': self.select = None
                if tag == 'form': self.current = None

        for page in ('jobs', 'dashboard'):
            with self.subTest(page=page):
                text = dashboard_html(page=page, csrf='safe')
                parser = FormParser(); parser.feed(text)
                self.assertEqual(len(parser.forms), 1)
                form = parser.forms[0]
                self.assertEqual(set(form['selects']), {'market','mode'})
                self.assertEqual(set(form['selects']['market']['values']), {'', *MARKETS})
                self.assertEqual(set(form['selects']['mode']['values']), {'', *MODES})
                self.assertTrue(all(select['required'] for select in form['selects'].values()))
                self.assertEqual(form['buttons'], 1)
                self.assertTrue(form['csrf'])
                self.assertEqual(form['groups'], 0)
                self.assertEqual(form['selects']['mode']['values'], ['', 'collect', 'predict', 'full'])
                self.assertNotIn('class="jobs-grid"', text)
                self.assertNotIn('class="quick-row"', text)

    def test_job_help_explains_automatic_indicators_and_three_patterns(self):
        text = dashboard_html(page='jobs', csrf='safe')
        for phrase in ('원본 시세 수집 → 지표 계산', '일봉 예측 → 주봉 예측', '수집 → 지표 계산 → 일봉 예측 → 주봉 예측', '자동', '세 가지'):
            self.assertIn(phrase, text)

    def test_backend_client_uses_fixed_path_without_auth_or_environment_proxy(self):
        from batch.web import BatchClient, NoRedirect
        client = BatchClient()
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.return_value = b'{"runs": []}'
        with patch.object(client.opener, 'open', return_value=stream) as opened:
            self.assertEqual(client.get('/runs'), {'runs': []})
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, 'http://batch:8001/runs')
        self.assertNotIn('Authorization', request.headers)
        self.assertTrue(any(isinstance(handler, NoRedirect) for handler in client.opener.handlers))
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'http://outside/'))

    def test_compose_publishes_only_the_python_web_not_batch_api(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        for filename in ('docker-compose.yaml', 'docker-compose.server.yml'):
            services = yaml.safe_load((root / filename).read_text(encoding='utf-8'))['services']
            self.assertEqual(services['batch-web']['command'], 'python -m batch.web')
            self.assertIn('8002', services['batch-web']['ports'][0])
            self.assertNotIn('ports', services['batch'])
            self.assertEqual(services['batch-web']['environment']['BATCH_API_URL'], 'http://batch:8001')

    def test_untrusted_names_logs_and_errors_are_escaped(self):
        job = {'run_id': 'a'*32, 'market': 'JP', 'mode': 'collect', 'status': 'failed', 'created_at': '2026-10-29T00:00:00+00:00', 'trigger_source': 'web', 'error_message': '<script>alert(1)</script>', 'tasks': []}
        text = dashboard_html(page='logs', runs=[job], schedules=[], summary={}, csrf='safe', detail=job, log='<script>alert(2)</script>')
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)

    def test_reference_layout_and_empty_data_are_honest(self):
        text = dashboard_html(page='dashboard', runs=[], schedules=[], summary={'registered_jobs': 6}, csrf='safe')
        self.assertIn('실행 이력이 없습니다', text)
        self.assertNotIn('회원 데이터', text)
        self.assertNotIn('오늘 성공</span><strong>42', text)
        self.assertIn('viewBox="0 0 640 250"', text)


if __name__ == '__main__':
    unittest.main()
