"""사용자가 요청한 도메인 접근·무인증 계약. 기존 작업·CSRF 계약은 유지합니다."""
import unittest
from pathlib import Path
from unittest.mock import Mock

from starlette.requests import Request
from batch.service import create_app
from batch.web import create_web_app, BatchClient


class LocalAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_starts_without_token_and_reads_runs(self):
        manager = Mock()
        manager.runs.return_value = []
        app = create_app(manager)
        endpoint = next(route.endpoint for route in app.routes if route.path == '/runs')
        request = Request({'type':'http','method':'GET','path':'/runs','headers':[]})
        response = await endpoint(request)
        self.assertEqual(response.status_code, 200)
        manager.runs.assert_called_once()

    async def test_web_starts_without_token_or_session_and_accepts_remote_host(self):
        client = Mock()
        client.get.side_effect = lambda path: {'/runs':{'runs':[]}, '/schedules':{'schedules':[]}, '/summary':{}}[path]
        app = create_web_app(client=client)
        endpoint = app.routes[0].endpoint
        scope = {'type':'http','scheme':'http','method':'GET','path':'/','query_string':b'','headers':[(b'host',b'localhost:4200')]}
        response = await endpoint(Request(scope))
        self.assertEqual(response.status_code, 200)
        text = response.body.decode()
        self.assertIn('대시보드', text)
        self.assertNotIn('type="password"', text)
        self.assertNotIn('/logout', text)
        self.assertNotIn('batch_session', response.headers.get('set-cookie',''))
        scope['headers'] = [(b'host',b'myserver.com:4200')]
        self.assertEqual((await endpoint(Request(scope))).status_code, 200)

    async def test_server_domain_form_preserves_csrf_and_origin_checks(self):
        from http.cookies import SimpleCookie
        from urllib.parse import urlencode
        client = Mock()
        client.get.side_effect = lambda path: {'/runs':{'runs':[]}, '/schedules':{'schedules':[]}, '/summary':{}}[path]
        client.post.return_value = {'run_id':'a'*32}
        endpoint = create_web_app(client=client).routes[0].endpoint
        scope = {'type':'http','scheme':'http','method':'GET','path':'/','query_string':b'', 'headers':[(b'host', b'myserver.com:4200')]}
        page = await endpoint(Request(scope))
        cookies = SimpleCookie(); cookies.load(page.headers['set-cookie'])
        csrf = cookies['batch_csrf'].value
        body = urlencode({'csrf':csrf, 'market':'JP', 'mode':'collect'}).encode()
        async def receive():
            return {'type':'http.request','body':body}
        scope.update(method='POST', path='/run', headers=[(b'host',b'myserver.com:4200'), (b'origin',b'http://myserver.com:4200'), (b'cookie',f'batch_csrf={csrf}'.encode()), (b'content-type',b'application/x-www-form-urlencoded')])
        self.assertEqual((await endpoint(Request(scope, receive))).status_code, 303)
        client.post.assert_called_once_with('/runs', {'market':'JP','mode':'collect'})
        client.post.reset_mock()
        scope['headers'][1] = (b'origin', b'http://other.example')
        self.assertEqual((await endpoint(Request(scope, receive))).status_code, 403)
        client.post.assert_not_called()

    async def test_client_no_longer_requires_a_token(self):
        client = BatchClient()
        self.assertEqual(client.base_url, 'http://batch:8001')

    async def test_compose_and_examples_no_longer_require_auth_variable(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        for name in ('docker-compose.yaml','docker-compose.server.yml','.env.example','.env.server.example'):
            self.assertNotIn('BATCH_ADMIN_TOKEN', (root/name).read_text(encoding='utf-8'))
        for name in ('docker-compose.yaml', 'docker-compose.server.yml'):
            services = yaml.safe_load((root/name).read_text(encoding='utf-8'))['services']
            self.assertNotIn('ports', services['batch'])
            self.assertEqual(services['viewer']['ports'][0], '${VIEWER_PORT:-4201}:8081')
            self.assertEqual(services['batch-web']['ports'][0], '${BATCH_UI_PORT:-4200}:8002')
            self.assertNotIn('BATCH_UI_BIND', (root/name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
