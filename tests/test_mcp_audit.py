"""MCP 접속·호출 기록은 주가 DB와 외부 네트워크 없이 검증한다."""
import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from mcp_audit import AuditStore, AuditMiddleware, request_metadata
import mcp_server as server


class AuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(tempfile.gettempdir()) / f'stock-mcp-test-{uuid.uuid4().hex}'
        self.directory.mkdir(mode=0o777)
        self.path = self.directory / 'usage.sqlite3'
        self.store = AuditStore(self.path, retention_days=90)

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_missing_db_is_not_created_by_reading(self):
        result = self.store.report({})
        self.assertFalse(result['available'])
        self.assertFalse(self.path.exists())

    def test_report_uses_readonly_connection_and_does_not_change_database(self):
        import sqlite3
        self.store.record(kind='tool', name='readme', status='success')
        before = self.path.read_bytes()
        connect = sqlite3.connect
        with patch('mcp_audit.sqlite3.connect', wraps=connect) as opened:
            self.store.report({})
        self.assertTrue(opened.call_args.kwargs['uri'])
        self.assertTrue(opened.call_args.args[0].endswith('?mode=ro'))
        self.assertEqual(self.path.read_bytes(), before)

    def test_persistence_counts_filters_pagination_and_safe_arguments(self):
        common = {'client': '<AI>', 'ip': '127.0.0.1'}
        self.store.record(kind='access', name='POST', status='200', **common)
        self.store.record(kind='tool', name='stock_data', status='success', arguments={
            'market': 'KR', 'code': '005930', 'authorization': 'SECRET', 'nested': {'secret': 'SECRET'}}, **common)
        self.store.record(kind='tool', name='stock_data_week', status='failed', error_type='ToolError', **common)
        result = AuditStore(self.path).report({})
        self.assertEqual(result['counts'], {'access': 1, 'tools': 2, 'errors': 1})
        self.assertEqual(result['total'], 3)
        self.assertNotIn('SECRET', str(result))
        self.assertIn('005930', str(result))
        self.assertEqual(sum(row['count'] for row in result['daily']), 3)
        self.assertEqual(result['clients'][0]['count'], 3)
        self.assertEqual(self.store.report({'kind': 'tool', 'tool': 'stock_data'})['total'], 1)
        self.assertEqual(self.store.report({'status': 'failed'})['total'], 1)
        self.assertEqual(self.store.report({'client': '<AI>'})['total'], 3)
        self.assertEqual(self.store.report({'client': "' OR 1=1 --"})['total'], 0)
        self.assertEqual(self.store.report({'page': '2'})['rows'], [])
        for filters in ({'kind': 'bad'}, {'page': '0'}, {'start': '2026-02-30'}, {'start': '2026-10-31', 'end': '2026-01-01'}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.store.report(filters)

    def test_retention_prunes_only_old_audit_events(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        old = (datetime.now(ZoneInfo('Asia/Seoul')) - timedelta(days=91)).isoformat()
        with patch('mcp_audit.now_iso', return_value=old):
            self.store.record(kind='tool', name='old', status='success')
        self.store.record(kind='tool', name='new', status='success')
        result = self.store.report({})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['rows'][0]['name'], 'new')

    async def test_real_mcp_dispatch_records_success_validation_unknown_and_async_errors(self):
        with patch.dict(os.environ, {'MCP_AUDIT_DB': str(self.path)}):
            await server.mcp.call_tool('readme', {})
            await server.mcp.call_tool('usage', {'tool': 'readme'})
            for name, args in [('stock_data', {}), ('no_such_tool', {}), ('usage', {'tool': 'bad'})]:
                with self.assertRaises(Exception):
                    await server.mcp.call_tool(name, args)
        result = self.store.report({'kind': 'tool'})
        self.assertEqual(result['counts']['tools'], 5)
        self.assertEqual(result['counts']['errors'], 3)

    async def test_stock_function_call_and_arguments_appear_in_default_dashboard_not_http_noise(self):
        from unittest.mock import Mock
        from starlette.requests import Request
        from batch.web import create_web_app
        with patch.dict(os.environ, {'MCP_AUDIT_DB': str(self.path)}), patch.object(server, '_rows', return_value=[]):
            await server.mcp.call_tool('stock_data', {'market': 'KR', 'code': '005930', 'limit': 20})
        self.store.record(kind='access', name='GET', status='404')
        backend = Mock()
        endpoint = create_web_app(client=backend, audit_store=self.store).routes[0].endpoint
        scope = {'type': 'http', 'scheme': 'http', 'method': 'GET', 'path': '/',
                 'query_string': b'page=mcp', 'headers': [(b'host', b'localhost')]}
        response = await endpoint(Request(scope))
        text = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('stock_data()', text)
        self.assertIn('005930', text)
        self.assertIn('&quot;market&quot;: &quot;KR&quot;', text)
        self.assertNotIn('<td>GET</td>', text)
        self.assertIn('함수 호출 상세 · 총 1건', text)
        scope['query_string'] = b'page=mcp&kind=access'
        text = (await endpoint(Request(scope))).body.decode()
        self.assertNotIn('stock_data()', text)
        self.assertIn('<td>GET</td>', text)
        self.assertIn('함수 호출 아님', text)
        backend.get.assert_not_called()

    async def test_audit_write_failure_does_not_break_readonly_tool(self):
        with patch('mcp_audit.AuditStore.record', side_effect=OSError('private secret')):
            with self.assertLogs('mcp_audit', level='ERROR') as logs:
                result = await server.mcp.call_tool('readme', {})
            self.assertTrue(result)
            self.assertNotIn('private secret', str(logs.output))

    async def test_cancelled_tool_is_recorded_without_changing_cancellation(self):
        started = asyncio.Event()
        async def waiting(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        with patch.dict(os.environ, {'MCP_AUDIT_DB': str(self.path)}), patch.object(server.FastMCP, 'call_tool', side_effect=waiting):
            task = asyncio.create_task(server.mcp.call_tool('readme', {}))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        result = self.store.report({})
        self.assertEqual(result['counts']['errors'], 1)
        self.assertEqual(result['rows'][0]['status'], 'cancelled')

    def test_http_mcp_session_records_client_and_tools_using_real_sdk(self):
        from starlette.testclient import TestClient
        headers = {'accept': 'application/json, text/event-stream', 'user-agent': 'AuditTestAI',
                   'host': 'localhost:8000'}
        with patch.dict(os.environ, {'MCP_AUDIT_DB': str(self.path)}):
            with TestClient(server.create_asgi_app(), headers=headers) as client:
                response = client.post('/mcp/', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                               'clientInfo': {'name': 'test-client', 'version': '1'}}})
                self.assertEqual(response.status_code, 200, response.text)
                session = response.headers['mcp-session-id']
                extra = {'mcp-session-id': session, 'mcp-protocol-version': '2025-03-26'}
                response = client.post('/mcp/', headers=extra, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                self.assertEqual(response.status_code, 202)
                response = client.post('/mcp/', headers=extra, json={'jsonrpc': '2.0', 'id': 2,
                    'method': 'tools/call', 'params': {'name': 'readme', 'arguments': {}}})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('stocksearcher-postgres-mcp', response.text)
                response = client.post('/mcp/', headers=extra, json={'jsonrpc': '2.0', 'id': 3,
                    'method': 'tools/call', 'params': {'name': 'stock_data', 'arguments': {}}})
                self.assertEqual(response.status_code, 200)
                self.assertIn('isError', response.text)
        result = self.store.report({})
        self.assertEqual(result['counts'], {'access': 4, 'tools': 2, 'errors': 1})
        self.assertEqual({row['client'] for row in result['rows']}, {'AuditTestAI'})
        self.assertNotIn(session, str(result))

    def test_pagination_dates_and_concurrent_writes(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime
        from zoneinfo import ZoneInfo
        def write(index):
            self.store.record(kind='tool', name='readme', status='success', client=str(index))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(55)))
        today = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
        first = self.store.report({'start': today, 'end': today})
        second = self.store.report({'page': '2'})
        self.assertEqual(first['total'], 55)
        self.assertEqual(len(first['rows']), 50)
        self.assertEqual(len(second['rows']), 5)
        self.assertFalse({row['id'] for row in first['rows']} & {row['id'] for row in second['rows']})
        self.store.record(kind='tool', name='bad_args', status='failed', arguments={'close_max': float('nan')})
        self.assertEqual(self.store.report({'tool': 'bad_args'})['total'], 1)

    async def test_http_logging_preserves_asgi_messages_and_ignores_secrets(self):
        async def app(scope, receive, send):
            self.assertEqual(request_metadata(scope)['ip'], '10.0.0.1')
            await send({'type': 'http.response.start', 'status': 202, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'secret response'})
        messages = []
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'method': 'POST', 'path': '/mcp/', 'client': ('10.0.0.1', 1234),
                 'headers': [(b'user-agent', b'TestAI'), (b'authorization', b'Bearer SECRET'),
                             (b'x-forwarded-for', b'FAKE')]}
        with patch.dict(os.environ, {'MCP_AUDIT_DB': str(self.path)}):
            await AuditMiddleware(app)(scope, None, send)
        result = self.store.report({})
        self.assertEqual(result['counts']['access'], 1)
        self.assertEqual(result['rows'][0]['status'], '202')
        self.assertEqual(result['rows'][0]['client'], 'TestAI')
        self.assertEqual(result['rows'][0]['ip'], '10.0.0.1')
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('secret response', str(result))
        self.assertEqual(messages[-1]['body'], b'secret response')

    def test_compose_uses_separate_persistent_volume_and_readonly_dashboard(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        for filename in ('docker-compose.yaml', 'docker-compose.server.yml'):
            config = yaml.safe_load((root / filename).read_text(encoding='utf-8'))
            services = config['services']
            self.assertIn('mcp-audit', config['volumes'])
            self.assertIn('mcp-audit:/var/lib/stock-mcp', services['mcp']['volumes'])
            self.assertIn('mcp-audit:/var/lib/stock-mcp:ro', services['batch-web']['volumes'])
            for service in ('mcp', 'batch-web'):
                self.assertEqual(services[service]['environment']['MCP_AUDIT_DB'], '/var/lib/stock-mcp/mcp_usage.sqlite3')
