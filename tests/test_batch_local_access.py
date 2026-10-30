"""사용자가 요청한 로컬 무인증 계약. 기존 작업·CSRF 계약은 유지합니다."""
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

    async def test_web_starts_without_token_or_session_and_rejects_remote_host(self):
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
        scope['headers'] = [(b'host',b'attacker.example')]
        self.assertEqual((await endpoint(Request(scope))).status_code, 400)

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
            self.assertTrue(services['viewer']['ports'][0].startswith('127.0.0.1:'))
            self.assertIn('127.0.0.1', services['batch-web']['ports'][0])


if __name__ == '__main__':
    unittest.main()
