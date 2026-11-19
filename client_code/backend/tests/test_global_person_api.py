"""운영 DB 없이 글로벌 인물 HTTP/무결성/동시성/수명주기를 검증한다."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
import time
import unittest
from unittest.mock import patch
from uuid import uuid1, uuid4

from fastapi.testclient import TestClient
import httpx
import uvicorn

from client_code.backend.main import create_app
from client_code.feature_vector import decode_vector, encode_vector

URL = '/api/v1/global-persons'
AUTH = '/api/v1/auth'
ACCOUNT = dict(username='admin', password='test-pass')
OBSERVED = '2026-11-18T03:00:00.000000Z'


def body(**changes):
    return dict(request_id=str(uuid4()), feature_vector=[1.0] + [0.0] * 511,
                observed_at=OBSERVED) | changes


def login(client):
    tokens = client.post(AUTH + '/login', json=ACCOUNT).json()
    client.headers['Authorization'] = 'Bearer ' + tokens['access_token']
    return tokens


class GlobalPersonAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'api.db'
        self.app = create_app(self.path)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.post(AUTH + '/signup', json=ACCOUNT | {'password_confirm': ACCOUNT['password']})
        self.tokens = login(self.client)
        self.service = self.app.state.global_person_service

    def count(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute('SELECT count(*) FROM global_person_manage').fetchone()[0]

    def test_flow_storage_and_unchanged_vector(self):
        response = self.client.get(URL)
        self.assertEqual(response.json(), {'feature_dimension': 512, 'items': []})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        payload = body(feature_vector=[1 / (512 ** .5)] * 512)
        response = self.client.post(URL, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        self.assertEqual(created['created_at'], created['updated_at'])
        self.assertEqual(created['last_seen_at'], OBSERVED)
        self.assertEqual(len(created['feature_vector']), 512)
        with closing(sqlite3.connect(self.path)) as db, db:
            blob, kind = db.execute('SELECT feature_vector,typeof(feature_vector) FROM global_person_manage').fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(kind, 'blob')
        self.assertEqual(len(blob), 2048)
        self.assertEqual(blob, encode_vector(payload['feature_vector']))
        self.assertEqual(created['feature_vector'], decode_vector(blob))
        self.assertEqual(tables - {'sqlite_sequence'}, {'users', 'server_runs', 'cameras', 'global_person_manage', 'events'})
        target = URL + f"/{created['global_person_id']}/last-seen"
        for observed in ('2026-11-19T00:00:00+09:00', OBSERVED, '2026-11-17T00:00:00Z'):
            changed = self.client.patch(target, json={'observed_at': observed})
            self.assertEqual(changed.status_code, 200)
            result = changed.json()
            self.assertEqual(result['last_seen_at'], '2026-11-18T15:00:00.000000Z')
            for field in ('feature_vector', 'created_at', 'updated_at'):
                self.assertEqual(result[field], created[field])
        self.assertEqual(self.client.get(URL).json()['items'], [result])

    def test_retry_utc_float32_normalization_and_conflict(self):
        payload = body()
        first = self.client.post(URL, json=payload).json()
        retry = payload | {'observed_at': '2026-11-18T12:00:00+09:00',
                           'feature_vector': [1.000000001] + [0] * 511}
        response = self.client.post(URL, json=retry)  # 응답 유실 후 동일 정규화 입력
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), first)
        self.client.patch(URL + f"/{first['global_person_id']}/last-seen",
                          json={'observed_at': '2026-11-19T00:00:00Z'})
        self.assertEqual(self.client.post(URL, json=payload).json()['last_seen_at'],
                         '2026-11-19T00:00:00.000000Z')
        for changes in ({'observed_at': '2026-11-20T00:00:00Z'},
                        {'feature_vector': [0.0, 1.0] + [0.0] * 510}):
            response = self.client.post(URL, json=payload | changes)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error']['code'], 'GLOBAL_PERSON_REQUEST_CONFLICT')
        self.assertEqual(self.count(), 1)

    def test_same_registration_concurrency(self):
        payload = body()
        barrier = Barrier(4)
        def send(_):
            barrier.wait()
            return self.client.post(URL, json=payload)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(send, range(4)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 200, 200, 201])
        self.assertEqual(len({r.json()['global_person_id'] for r in results}), 1)
        self.assertEqual(self.count(), 1)

    def test_different_registrations_and_last_seen_concurrency(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.client.post(URL, json=body()), range(4)))
        self.assertTrue(all(r.status_code == 201 for r in results))
        ids = [r.json()['global_person_id'] for r in results]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual([r['global_person_id'] for r in self.client.get(URL).json()['items']], sorted(ids))
        target = URL + f'/{ids[0]}/last-seen'
        barrier = Barrier(4)
        times = [f'2026-11-{day}T00:00:00Z' for day in (19, 22, 20, 21)]
        def send(observed):
            barrier.wait()
            return self.client.patch(target, json={'observed_at': observed})
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(send, times))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        items = self.client.get(URL).json()['items']
        self.assertEqual(next(r for r in items if r['global_person_id'] == ids[0])['last_seen_at'],
                         '2026-11-22T00:00:00.000000Z')

    def test_invalid_requests_and_redaction(self):
        vectors = [[], [0] * 512, [1] * 511, [1] * 513, ['sensitive-vector'] * 512,
                   [True] + [0] * 511, [None] * 512, [[1]] * 512,
                   [1e40] + [0] * 511, [2] + [0] * 511]
        for vector in vectors:
            with self.subTest(vector_type=type(vector[0]).__name__ if vector else 'empty'):
                response = self.client.post(URL, json=body(feature_vector=vector))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn('sensitive-vector', response.text)
                self.assertEqual(response.json()['error']['field'], 'feature_vector')
        for value in (float('nan'), float('inf'), -float('inf')):
            response = self.client.post(URL, content=json.dumps(body(feature_vector=[value] + [0] * 511)),
                                        headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 422)
        for changes in ({'request_id': str(uuid1())}, {'request_id': 123}, {'request_id': None},
                        {'observed_at': '2026-11-18T00:00:00'}, {'observed_at': 123},
                        {'observed_at': '123'}, {'observed_at': '0001-01-01T00:00:00+09:00'},
                        {'observed_at': None}, {'global_person_id': 1}):
            self.assertEqual(self.client.post(URL, json=body(**changes)).status_code, 422)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.client.post(URL, json={}).status_code, 422)
        target = URL + '/1/last-seen'
        for payload in ({}, {'observed_at': None}, {'observed_at': OBSERVED, 'feature_vector': [1]}):
            self.assertEqual(self.client.patch(target, json=payload).status_code, 422)

    def test_auth_on_all_routes(self):
        calls = [('GET', URL, None), ('POST', URL, body()),
                 ('PATCH', URL + '/1/last-seen', {'observed_at': OBSERVED})]
        for token in ('', 'invalid', self.tokens['refresh_token']):
            for method, target, payload in calls:
                response = self.client.request(method, target, json=payload,
                    headers={'Authorization': 'Bearer ' + token})
                self.assertEqual(response.status_code, 401)
        with patch.object(self.app.state.auth_service, 'clock',
                          return_value=self.app.state.auth_service.clock() + 901):
            for method, target, payload in calls:
                self.assertEqual(self.client.request(method, target, json=payload).status_code, 401)
        self.client.post(AUTH + '/logout', json={'refresh_token': self.tokens['refresh_token']})
        for method, target, payload in calls:
            self.assertEqual(self.client.request(method, target, json=payload).status_code, 401)
        self.assertEqual(self.count(), 0)

    def test_missing_and_invalid_id(self):
        for value in ('0', '-1', 'abc', '1.5'):
            self.assertEqual(self.client.patch(URL + f'/{value}/last-seen',
                json={'observed_at': OBSERVED}).status_code, 422)
        result = self.client.patch(URL + '/999/last-seen', json={'observed_at': OBSERVED})
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.json()['error']['code'], 'GLOBAL_PERSON_NOT_FOUND')

    def test_registration_flush_failure_rolls_back_and_can_retry(self):
        payload = body()
        real_add = self.service.repository.add
        def fail(*args):
            real_add(*args)
            raise RuntimeError('private-db-secret')
        with patch.object(self.service.repository, 'add', side_effect=fail):
            response = self.client.post(URL, json=payload)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private-db-secret', response.text)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.client.post(URL, json=payload).status_code, 201)

    def test_commit_failure_not_cached(self):
        payload = body()
        session_type = self.service.session_factory.class_
        with patch.object(session_type, 'commit', side_effect=RuntimeError('commit-private')):
            response = self.client.post(URL, json=payload)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.client.post(URL, json=payload).status_code, 201)

    def test_last_seen_failure_rolls_back(self):
        created = self.client.post(URL, json=body()).json()
        real_update = self.service.repository.update_last_seen
        def fail(*args):
            real_update(*args)
            raise RuntimeError('private-update')
        with patch.object(self.service.repository, 'update_last_seen', side_effect=fail):
            response = self.client.patch(URL + f"/{created['global_person_id']}/last-seen",
                json={'observed_at': '2026-11-22T00:00:00Z'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get(URL).json()['items'], [created])

    def test_bad_stored_vector_fails_instead_of_skipping(self):
        self.client.post(URL, json=body())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE global_person_manage SET feature_vector=?', (bytes(2048),))
        result = self.client.get(URL)
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.json()['error']['code'], 'INTERNAL_SERVER_ERROR')

    def test_database_constraint_and_autoincrement(self):
        created = self.client.post(URL, json=body()).json()
        with closing(sqlite3.connect(self.path)) as db, db:
            for invalid in (b'x', 'x' * 2048, None):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('UPDATE global_person_manage SET feature_vector=?', (invalid,))
            db.execute('DELETE FROM global_person_manage')
        second = self.client.post(URL, json=body()).json()
        self.assertGreater(second['global_person_id'], created['global_person_id'])


class GlobalPersonLifecycleTests(unittest.TestCase):
    def test_existing_three_tables_are_preserved(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'existing.db'
            with TestClient(create_app(path)) as client:
                client.post(AUTH + '/signup', json=ACCOUNT | {'password_confirm': ACCOUNT['password']})
                login(client)
                camera = client.post('/api/v1/cameras', json={
                    'name': 'preserved', 'camera_type': 'webcam', 'webcam_index': 0}).json()
            # 임시 DB를 기존 3개 테이블만 있는 상태로 만든다. 운영 DB에는 접근하지 않는다.
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('DROP TABLE events')
                db.execute('DROP TABLE global_person_manage')
            with TestClient(create_app(path)) as client:
                self.assertFalse(client.get(AUTH + '/signup-status').json()['signup_required'])
                login(client)
                self.assertEqual(client.get('/api/v1/cameras/1').json(), camera)
                self.assertEqual(client.get(URL).json()['items'], [])
                self.assertEqual(client.post(URL, json=body()).status_code, 201)

    def test_restart_retains_people_and_discards_retry_cache(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'restart.db'
            payload = body()
            with TestClient(create_app(path)) as client:
                client.post(AUTH + '/signup', json=ACCOUNT | {'password_confirm': ACCOUNT['password']})
                tokens = login(client)
                created = client.post(URL, json=payload).json()
            with TestClient(create_app(path)) as client:
                client.headers['Authorization'] = 'Bearer ' + tokens['access_token']
                self.assertEqual(client.get(URL).status_code, 401)
                login(client)
                self.assertEqual(client.get(URL).json()['items'], [created])
                self.assertEqual(client.app.state.global_person_service._requests, {})
                # 과거 미확정 요청 재전송은 PC에서 금지한다. 여기서는 새 관측만 등록한다.
                self.assertEqual(client.post(URL, json=body()).status_code, 201)

    def test_real_uvicorn_http(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'http.db'
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(create_app(path), log_level='critical'))
            thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 15
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(server.started)
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=10) as client:
                    self.assertEqual(client.get(URL).status_code, 401)
                    client.post(AUTH + '/signup', json=ACCOUNT | {'password_confirm': ACCOUNT['password']})
                    login(client)
                    payload = body()
                    result = client.post(URL, json=payload)
                    self.assertEqual(result.status_code, 201, result.text)
                    self.assertEqual(client.post(URL, json=payload).status_code, 200)
                    person_id = result.json()['global_person_id']
                    self.assertEqual(client.patch(URL + f'/{person_id}/last-seen',
                        json={'observed_at': OBSERVED}).status_code, 200)
                    self.assertEqual(len(client.get(URL).json()['items']), 1)
                    paths = client.get('/openapi.json').json()['paths']
                    self.assertIn(URL, paths)
                    self.assertIn(URL + '/{global_person_id}/last-seen', paths)
                    self.assertIn('/api/v1/events', paths)
                    self.assertNotIn('/api/v1/events/{event_id}/global-match', paths)
            finally:
                server.should_exit = True
                thread.join(15)
                sock.close()
                self.assertFalse(thread.is_alive())
