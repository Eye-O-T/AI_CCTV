"""카메라·운영 DB 없이 공용 Manager 결정/큐/저장 유실/수명을 검증한다."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import requests
import uvicorn

from client_code.backend.main import create_app
from client_code.reid.api_client import GlobalPersonApiClient, GlobalPersonApiError, parse_record
from client_code.reid.global_person_manager import GlobalPersonManager
from client_code.reid.tasks import MatchTask, Observation

NOW = datetime(2026, 11, 18, 3, tzinfo=timezone.utc)
V1 = tuple([1.] + [0.] * 511)
V2 = tuple([0., 1.] + [0.] * 510)


def wait_for(check, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.01)
    raise AssertionError('비동기 작업이 제한 시간에 완료되지 않았습니다.')


def observation(camera=1, local=1, **changes):
    values = dict(task_id=uuid4(), camera_id=camera, execution_id=uuid4().hex,
                  tracking_generation=0, local_person_id=local, observed_at=NOW)
    return Observation(**(values | changes))


def record(person_id, vector=V1):
    return parse_record(dict(global_person_id=person_id, feature_vector=list(vector),
        created_at=NOW.isoformat(), updated_at=NOW.isoformat(), last_seen_at=NOW.isoformat()))


class FakeApi:
    def __init__(self, rows=()):
        self.rows = {row.global_person_id: row for row in rows}
        self.requests = {}
        self.calls = []
        self.lose_create = False
        self.lose_update = False
        self.list_error = None
        self.save_error = None
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.closed = False

    def list_people(self):
        self.calls.append(('list',))
        if self.list_error:
            raise self.list_error
        return list(self.rows.values())

    def _before_save(self):
        self.entered.set()
        if not self.release.wait(3):
            raise GlobalPersonApiError('CONNECTION_ERROR', True)
        if self.save_error:
            raise self.save_error

    def create_person(self, request_id, vector, observed_at):
        self.calls.append(('create', request_id, vector, observed_at))
        self._before_save()
        if request_id not in self.requests:
            person_id = max(self.rows, default=0) + 1
            self.rows[person_id] = replace(record(person_id, vector), last_seen_at=observed_at)
            self.requests[request_id] = person_id
        if self.lose_create:
            self.lose_create = False
            raise GlobalPersonApiError('CONNECTION_ERROR', True)
        return self.rows[self.requests[request_id]]

    def update_last_seen(self, person_id, observed_at):
        self.calls.append(('update', person_id))
        self._before_save()
        self.rows[person_id] = replace(self.rows[person_id], last_seen_at=max(self.rows[person_id].last_seen_at, observed_at))
        if self.lose_update:
            self.lose_update = False
            raise GlobalPersonApiError('CONNECTION_ERROR', True)
        return self.rows[person_id]

    def close(self):
        self.closed = True


class ManagerTests(unittest.TestCase):
    def manager(self, api=None, **kwargs):
        api = api or FakeApi()
        manager = GlobalPersonManager(api, **kwargs)
        def cleanup():
            api.release.set()
            manager.request_stop()
            manager.join(4)
            self.assertFalse(manager.is_alive())
        self.addCleanup(cleanup)
        manager.start()
        return manager, api

    def submit(self, manager, camera=1, vector=V1, obs=None):
        obs = obs or observation(camera)
        self.assertTrue(manager.begin_observation(obs))
        task = MatchTask(obs, vector)
        self.assertTrue(manager.submit(task))
        return task

    def result(self, manager):
        received = []
        wait_for(lambda: received.extend(manager.take_results()) or bool(received))
        return received[0]

    def test_empty_gallery_new_then_other_camera_existing(self):
        manager, api = self.manager()
        self.assertTrue(manager.wait_until_ready(3))
        first = self.submit(manager, 1)
        a = self.result(manager)
        self.submit(manager, 2)
        b = self.result(manager)
        self.assertEqual((a.mode, b.mode), ('new', 'existing'))
        self.assertEqual(a.global_person_id, b.global_person_id)
        self.assertEqual(len(api.rows), 1)
        self.assertFalse(manager.submit(first))

    def test_restore_tie_uses_smallest_id(self):
        manager, api = self.manager(FakeApi([record(10), record(2)]))
        self.assertTrue(manager.wait_until_ready(3))
        self.submit(manager)
        result = self.result(manager)
        self.assertEqual(result.global_person_id, 2)
        self.assertEqual(result.mode, 'existing')
        self.assertEqual(api.calls[-1], ('update', 2))

    def test_threshold_and_below_threshold(self):
        manager, api = self.manager(FakeApi([record(1)]), threshold=1.0)
        self.assertTrue(manager.wait_until_ready(3))
        first = self.submit(manager)
        self.assertEqual(self.result(manager).mode, 'existing')  # 경계 1.0 포함
        manager.end_observation(first.observation.task_id)
        self.submit(manager, vector=V2)
        self.assertEqual(self.result(manager).mode, 'new')
        self.assertEqual(len(api.rows), 2)

    def test_same_camera_collision_and_release(self):
        manager, api = self.manager(FakeApi([record(1)]))
        self.assertTrue(manager.wait_until_ready(3))
        first = self.submit(manager, obs=observation(1, 1))
        self.assertEqual(self.result(manager).global_person_id, 1)
        self.submit(manager, obs=observation(1, 2))
        second = self.result(manager)
        self.assertEqual(second.mode, 'new')
        self.assertNotEqual(second.global_person_id, 1)
        manager.end_observation(first.observation.task_id)
        self.submit(manager, obs=observation(1, 3))
        self.assertEqual(self.result(manager).global_person_id, 1)

    def test_lost_create_response_keeps_same_decision(self):
        api = FakeApi()
        api.release.clear()
        api.lose_create = True
        manager, api = self.manager(api)
        self.assertTrue(manager.wait_until_ready(3))
        first = self.submit(manager)
        self.assertTrue(api.entered.wait(2))
        self.submit(manager, 2)
        api.release.set()
        wait_for(lambda: manager.state == 'ERROR')
        self.assertEqual(len(api.rows), 1)  # DB 커밋 뒤 응답만 유실
        self.assertEqual(len([c for c in api.calls if c[0] == 'create']), 1)
        self.assertEqual(manager.take_results(), [])
        self.assertTrue(manager.retry_pending())
        results = []
        wait_for(lambda: results.extend(manager.take_results()) or len(results) == 2)
        self.assertEqual(len({r.global_person_id for r in results}), 1)
        creates = [c for c in api.calls if c[0] == 'create']
        self.assertEqual(creates[0], creates[1])
        self.assertEqual(creates[0][1], first.observation.task_id)
        self.assertEqual(len(api.rows), 1)

    def test_lost_update_response_can_retry(self):
        api = FakeApi([record(1)])
        api.lose_update = True
        manager, _ = self.manager(api)
        self.assertTrue(manager.wait_until_ready(3))
        self.submit(manager)
        wait_for(lambda: manager.state == 'ERROR')
        self.assertTrue(manager.retry_pending())
        self.assertEqual(self.result(manager).global_person_id, 1)
        self.assertEqual([c[0] for c in api.calls], ['list', 'update', 'update'])

    def test_gallery_failure_does_not_become_empty_success(self):
        api = FakeApi([record(4)])
        api.list_error = GlobalPersonApiError('SERVER_ERROR', True)
        manager, _ = self.manager(api)
        wait_for(lambda: manager.state == 'ERROR')
        obs = observation()
        manager.begin_observation(obs)
        self.assertFalse(manager.submit(MatchTask(obs, V1)))
        api.list_error = None
        self.assertTrue(manager.retry_pending())
        self.assertTrue(manager.wait_until_ready(3))
        self.assertTrue(manager.submit(MatchTask(obs, V1)))
        self.assertEqual(self.result(manager).global_person_id, 4)

    def test_unauthorized_requires_discard_not_retry(self):
        api = FakeApi()
        api.save_error = GlobalPersonApiError('INVALID_ACCESS_TOKEN')
        manager, _ = self.manager(api)
        self.assertTrue(manager.wait_until_ready(3))
        self.submit(manager)
        wait_for(lambda: manager.state == 'ERROR')
        self.assertFalse(manager.retry_pending())
        self.assertEqual(api.rows, {})

    def test_end_during_http_keeps_saved_candidate_without_binding(self):
        api = FakeApi()
        api.release.clear()
        manager, _ = self.manager(api)
        self.assertTrue(manager.wait_until_ready(3))
        first = self.submit(manager)
        self.assertTrue(api.entered.wait(2))
        manager.end_observation(first.observation.task_id)
        api.release.set()
        wait_for(lambda: len(api.rows) == 1 and manager.state == 'READY')
        self.submit(manager, 2)
        result = self.result(manager)
        self.assertEqual(result.observation.camera_id, 2)
        self.assertEqual(result.mode, 'existing')
        self.assertEqual(len(api.rows), 1)

    def test_queue_bound_nonblocking_and_stale_queued_task(self):
        api = FakeApi()
        api.release.clear()
        manager, _ = self.manager(api, queue_size=1)
        self.assertTrue(manager.wait_until_ready(3))
        self.submit(manager)
        self.assertTrue(api.entered.wait(2))
        second = self.submit(manager, 2)
        third = MatchTask(observation(3), V1)
        manager.begin_observation(third.observation)
        started = time.monotonic()
        self.assertFalse(manager.submit(third))
        self.assertLess(time.monotonic() - started, .1)
        manager.end_camera(2, second.observation.execution_id)
        api.release.set()
        self.result(manager)
        wait_for(lambda: manager._queue.empty())
        self.assertEqual(len([c for c in api.calls if c[0] in ('create', 'update')]), 1)

    def test_same_local_key_new_task_replaces_old(self):
        manager, _ = self.manager()
        self.assertTrue(manager.wait_until_ready(3))
        first = self.submit(manager)
        old = self.result(manager)
        obs = replace(first.observation, task_id=uuid4())
        self.submit(manager, obs=obs)
        result = self.result(manager)
        self.assertEqual(result.mode, 'existing')
        self.assertEqual(result.global_person_id, old.global_person_id)
        self.assertFalse(manager.submit(first))

    def test_stop_while_error_wakes_and_clears_connections(self):
        api = FakeApi()
        api.save_error = GlobalPersonApiError('SERVER_ERROR', True)
        manager, _ = self.manager(api)
        self.assertTrue(manager.wait_until_ready(3))
        self.submit(manager)
        wait_for(lambda: manager.state == 'ERROR')
        manager.request_stop()
        manager.join(2)
        self.assertFalse(manager.is_alive())
        self.assertTrue(api.closed)
        self.assertEqual(manager.take_results(), [])
        self.assertFalse(manager.retry_pending())
        manager.request_stop()
        self.assertEqual(manager.state, 'STOPPED')
        self.assertFalse(manager.begin_observation(observation()))

    def test_invalid_saved_identity_does_not_create_local_binding(self):
        api = FakeApi([record(1)])
        manager, _ = self.manager(api)
        self.assertTrue(manager.wait_until_ready(3))
        self.submit(manager, obs=observation(1, 1))
        self.result(manager)
        # 같은 카메라 점유로 신규를 요청했는데 기존 ID를 반환하는 잘못된 응답.
        with patch.object(api, 'create_person', return_value=record(1)):
            second = self.submit(manager, obs=observation(1, 2))
            wait_for(lambda: manager.state == 'ERROR')
        self.assertEqual(manager.error_code, 'INVALID_RESPONSE')
        self.assertEqual(manager.take_results(), [])
        self.assertNotIn(second.observation.task_id, manager._bindings)


class ClientValidationTests(unittest.TestCase):
    def setUp(self):
        self.client = GlobalPersonApiClient('http://127.0.0.1:1234', 'private-token')
        self.addCleanup(self.client.close)

    def test_invalid_gallery_not_silently_skipped(self):
        for payload in ({'feature_dimension': 512, 'items': [{}]},
                        {'feature_dimension': True, 'items': []},
                        {'feature_dimension': 511, 'items': []}):
            with patch.object(self.client, '_request', return_value=payload):
                with self.assertRaises(GlobalPersonApiError) as error:
                    self.client.list_people()
                self.assertEqual(error.exception.code, 'INVALID_RESPONSE')

    def test_network_exception_redacts_token_and_url(self):
        with patch.object(self.client._http, 'request', side_effect=requests.ConnectionError('private-token')):
            with self.assertRaises(GlobalPersonApiError) as error:
                self.client.list_people()
        self.assertEqual(error.exception.code, 'CONNECTION_ERROR')
        self.assertNotIn('private-token', str(error.exception))

    def test_http_errors_and_malformed_json_are_sanitized(self):
        expected = {401: 'INVALID_ACCESS_TOKEN', 404: 'GLOBAL_PERSON_NOT_FOUND',
                    409: 'GLOBAL_PERSON_REQUEST_CONFLICT', 422: 'VALIDATION_ERROR',
                    500: 'SERVER_ERROR', 302: 'UNEXPECTED_HTTP_STATUS', 200: 'INVALID_RESPONSE'}
        for status, code in expected.items():
            response = requests.Response()
            response.status_code = status
            response._content = b'private-response-not-json'
            response._content_consumed = True
            with patch.object(self.client._http, 'request', return_value=response) as request:
                with self.assertRaises(GlobalPersonApiError) as error:
                    self.client.list_people()
                self.assertEqual(error.exception.code, code)
                self.assertNotIn('private', str(error.exception))
                self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_bad_response_dates_and_vector(self):
        payload = dict(global_person_id=1, feature_vector=list(V1), created_at=NOW.isoformat(),
                       updated_at=NOW.isoformat(), last_seen_at=NOW.isoformat())
        for changes in ({'global_person_id': True}, {'feature_vector': [0.] * 512},
                        {'last_seen_at': '2026-11-18T00:00:00'}, {'created_at': 123}):
            with self.assertRaises(GlobalPersonApiError):
                parse_record(payload | changes)

    def test_manager_import_does_not_load_ai_or_qt(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'import sys; from reid.global_person_manager import GlobalPersonManager; '
            'assert not any(k.startswith(("torch", "PyQt5", "ultralytics")) for k in sys.modules)'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


class ManagerHTTPIntegrationTests(unittest.TestCase):
    def test_real_backend_new_existing_and_restored_gallery(self):
        with TemporaryDirectory() as tmp:
            app = create_app(Path(tmp) / 'manager.db')
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='critical'))
            thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
            thread.start()
            managers = []
            try:
                wait_for(lambda: server.started, 12)
                base = f'http://127.0.0.1:{port}'
                account = {'username': 'admin', 'password': 'test-pass'}
                response = requests.post(base + '/api/v1/auth/signup',
                    json=account | {'password_confirm': 'test-pass'}, timeout=5)
                self.assertEqual(response.status_code, 201)
                token = requests.post(base + '/api/v1/auth/login', json=account, timeout=5).json()['access_token']
                def create_manager():
                    api = GlobalPersonApiClient(base, token)
                    manager = GlobalPersonManager(api)
                    managers.append(manager)
                    manager.start()
                    self.assertTrue(manager.wait_until_ready(4))
                    return manager
                def process(manager, camera):
                    obs = observation(camera)
                    manager.begin_observation(obs)
                    self.assertTrue(manager.submit(MatchTask(obs, V1)))
                    received = []
                    wait_for(lambda: received.extend(manager.take_results()) or bool(received))
                    return received[0]
                manager = create_manager()
                first = process(manager, 1)
                second = process(manager, 2)
                self.assertEqual((first.mode, second.mode), ('new', 'existing'))
                self.assertEqual(first.global_person_id, second.global_person_id)
                manager.request_stop()
                manager.join(3)
                restored = create_manager()
                third = process(restored, 1)
                self.assertEqual(third.mode, 'existing')
                self.assertEqual(third.global_person_id, first.global_person_id)
                response = requests.get(base + '/api/v1/global-persons',
                    headers={'Authorization': 'Bearer ' + token}, timeout=5)
                self.assertEqual(len(response.json()['items']), 1)
                # 실제 DB 커밋·서버 응답 후 PC에서 응답 유실을 모사한다.
                original_request = restored.api._request
                dropped = False
                def lose_response(method, *args, **kwargs):
                    nonlocal dropped
                    payload = original_request(method, *args, **kwargs)
                    if method == 'POST' and not dropped:
                        dropped = True
                        raise GlobalPersonApiError('CONNECTION_ERROR', True)
                    return payload
                with patch.object(restored.api, '_request', side_effect=lose_response):
                    obs = observation(2, 2)
                    restored.begin_observation(obs)
                    self.assertTrue(restored.submit(MatchTask(obs, V2)))
                    wait_for(lambda: restored.state == 'ERROR')
                    self.assertTrue(restored.retry_pending())
                    received = []
                    wait_for(lambda: received.extend(restored.take_results()) or bool(received))
                    self.assertEqual(received[0].mode, 'new')
                response = requests.get(base + '/api/v1/global-persons',
                    headers={'Authorization': 'Bearer ' + token}, timeout=5)
                self.assertEqual(len(response.json()['items']), 2)
            finally:
                for manager in managers:
                    manager.request_stop()
                    manager.join(7)
                    self.assertFalse(manager.is_alive())
                server.should_exit = True
                thread.join(10)
                sock.close()
                self.assertFalse(thread.is_alive())
