"""임시 DB의 카메라 HTTP 계약·동시성·롤백·재시작 검증."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
import socket
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import uvicorn

from client_code.backend.main import create_app

AUTH = '/api/v1/auth'
URL = '/api/v1/cameras'
SIGNUP = dict(username='admin', password='test-pass', password_confirm='test-pass')
LOGIN = dict(username='admin', password='test-pass')
CAM = dict(name='정문', camera_type='webcam', webcam_index=0)


def login(client):
    tokens = client.post(AUTH + '/login', json=LOGIN).json()
    client.headers['Authorization'] = 'Bearer ' + tokens['access_token']
    return tokens


class CameraAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'api.db'
        self.app = create_app(self.path)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.post(AUTH + '/signup', json=SIGNUP)
        self.tokens = login(self.client)

    def test_flow(self):
        self.assertEqual(self.client.get(URL).json(), {'items': []})
        created = self.client.post(URL, json=CAM)
        self.assertEqual(created.status_code, 201)
        location = created.headers['Location']
        body = created.json()
        self.assertEqual(self.client.get(location).json(), body)
        self.assertEqual(body['created_at'], body['updated_at'])
        unchanged = self.client.patch(location, json={'name': ' 정문 '}).json()
        self.assertEqual(unchanged['updated_at'], body['updated_at'])
        disabled = self.client.patch(location, json={'name': '출입구', 'is_enabled': False})
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()['is_enabled'])
        self.assertEqual(self.client.get(URL + '?is_enabled=true').json(), {'items': []})
        items = self.client.get(URL + '?is_enabled=false').json()['items']
        self.assertEqual(len(items), 1)
        self.assertNotIn('webcam_index', items[0])
        self.assertNotIn('rtsp_url', items[0])
        self.assertEqual(self.client.post(URL, json=CAM).status_code, 409)
        self.assertEqual(self.client.patch(location, json={'is_enabled': True}).status_code, 200)

    def test_edge_and_patch_final_state(self):
        edge = dict(name='후문', camera_type='edge_node', rtsp_url='rtsp://192.0.2.10/live',
                    edge_api_base_url='http://192.0.2.10:8001')
        response = self.client.post(URL, json=edge)
        self.assertEqual(response.status_code, 201)  # 문서용 도달 불가 장치도 등록
        location = response.headers['Location']
        self.assertEqual(self.client.patch(location, json={'edge_api_base_url': None}).status_code, 200)
        self.assertIsNone(self.client.get(location).json()['edge_api_base_url'])
        for body in ({'rtsp_url': None}, {'webcam_index': 1}):
            self.assertEqual(self.client.patch(location, json=body).status_code, 422)
        self.assertEqual(self.client.post(URL, json=edge).json()['error']['field'], 'rtsp_url')

    def test_validation_not_found_and_secret_redaction(self):
        location = self.client.post(URL, json=CAM).headers['Location']
        for body in ({}, {'name': None}, {'camera_type': 'webcam'}, {'camera_id': 2},
                     {'webcam_index': True}, {'is_enabled': 1}, {'webcam_index': None}):
            self.assertEqual(self.client.patch(location, json=body).status_code, 422)
        for value in ('0', '-1', 'abc'):
            self.assertEqual(self.client.get(URL + '/' + value).status_code, 422)
        for value in ('1', 'True', ''):
            self.assertEqual(self.client.get(URL + '?is_enabled=' + value).status_code, 422)
        self.assertEqual(self.client.get(URL + '/999').status_code, 404)
        self.assertEqual(self.client.patch(URL + '/999', json={'name': 'x'}).status_code, 404)
        result = self.client.post(URL, json=dict(name='x', camera_type='edge_node',
                                 rtsp_url='rtsp://user:private-secret@host/live'))
        self.assertEqual(result.status_code, 422)
        self.assertNotIn('private-secret', result.text)
        self.assertEqual(result.json()['error']['field'], 'rtsp_url')

    def test_auth_all_routes(self):
        for token in (None, 'bad', self.tokens['refresh_token']):
            headers = {} if token is None else {'Authorization': 'Bearer ' + token}
            for method, path, body in (('GET', URL, None), ('GET', URL + '/1', None),
                                       ('POST', URL, CAM), ('PATCH', URL + '/1', {'name': 'x'})):
                response = self.client.request(method, path, json=body, headers={'Authorization': headers.get('Authorization', '')})
                self.assertEqual(response.status_code, 401)
        self.client.post(AUTH + '/logout', json={'refresh_token': self.tokens['refresh_token']})
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_expired_access(self):
        service = self.app.state.auth_service
        with patch.object(service, 'clock', return_value=service.clock() + 901):
            self.assertEqual(self.client.get(URL).status_code, 401)

    def test_conflict_rolls_back_all_changes(self):
        self.client.post(URL, json=CAM)
        second = self.client.post(URL, json=CAM | {'webcam_index': 1}).json()
        location = URL + '/' + str(second['camera_id'])
        result = self.client.patch(location, json={'name': '변경되면 안 됨', 'webcam_index': 0})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.get(location).json(), second)

    def test_failure_after_flush_rolls_back(self):
        repo = self.app.state.camera_service.repository
        original = repo.add
        def fail(*args):
            original(*args)
            raise RuntimeError('private database details')
        with patch.object(repo, 'add', side_effect=fail):
            result = self.client.post(URL, json=CAM)
        self.assertEqual(result.status_code, 500)
        self.assertNotIn('private database details', result.text)
        self.assertEqual(self.client.get(URL).json(), {'items': []})
        location = self.client.post(URL, json=CAM).headers['Location']
        old = self.client.get(location).json()
        original_update = repo.update
        def fail_update(*args):
            original_update(*args)
            raise RuntimeError('private database details')
        with patch.object(repo, 'update', side_effect=fail_update):
            self.assertEqual(self.client.patch(location, json={'name': '실패'}).status_code, 500)
        self.assertEqual(self.client.get(location).json(), old)

    def parallel(self, calls):
        barrier = Barrier(len(calls))
        def run(call):
            barrier.wait(timeout=10)
            return call()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(run, calls))

    def test_concurrent_create_and_patch_conflicts(self):
        results = self.parallel([lambda: self.client.post(URL, json=CAM)] * 2)
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        self.client.post(URL, json=CAM | {'webcam_index': 1})
        results = self.parallel([
            lambda: self.client.patch(URL + '/1', json={'webcam_index': 2}),
            lambda: self.client.patch(URL + '/2', json={'webcam_index': 2})])
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])

    def test_concurrent_disjoint_patch_preserves_both(self):
        self.client.post(URL, json=CAM)
        results = self.parallel([
            lambda: self.client.patch(URL + '/1', json={'name': '새 이름'}),
            lambda: self.client.patch(URL + '/1', json={'is_enabled': False})])
        self.assertEqual([r.status_code for r in results], [200, 200])
        result = self.client.get(URL + '/1').json()
        self.assertEqual(result['name'], '새 이름')
        self.assertFalse(result['is_enabled'])


class CameraLifecycleTests(unittest.TestCase):
    def test_restart_retains_camera(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'restart.db'
            with TestClient(create_app(path)) as client:
                client.post(AUTH + '/signup', json=SIGNUP)
                tokens = login(client)
                original = client.post(URL, json=CAM).json()
            with TestClient(create_app(path)) as client:
                client.headers['Authorization'] = 'Bearer ' + tokens['access_token']
                self.assertEqual(client.get(URL).status_code, 401)
                login(client)
                self.assertEqual(client.get(URL + '/1').json(), original)

    def test_live_http(self):
        with TemporaryDirectory() as temp, socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(create_app(Path(temp) / 'live.db'), log_level='critical'))
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
                    client.post(AUTH + '/signup', json=SIGNUP)
                    login(client)
                    response = client.post(URL, json=CAM)
                    self.assertEqual(response.status_code, 201)
                    location = response.headers['Location']
                    self.assertEqual(client.get(location).json(), response.json())
                    self.assertEqual(client.patch(location, json={'is_enabled': False}).status_code, 200)
                    self.assertEqual(client.get(URL + '?is_enabled=true').json(), {'items': []})
                    self.assertEqual(client.post(URL, json=CAM).status_code, 409)
                    self.assertEqual(client.get(URL + '/999').status_code, 404)
                    self.assertEqual(client.patch(location, json={}).status_code, 422)
                    paths = client.get('/openapi.json').json()['paths']
                    self.assertEqual(set(paths[URL]), {'get', 'post'})
                    self.assertEqual(set(paths[URL + '/{camera_id}']), {'get', 'patch'})
            finally:
                server.should_exit = True
                thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
