"""임시 DB/설정/파일만 사용하는 이벤트 인증·저장소·HTTP 검증."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
import json
import os
import socket
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from threading import Barrier, Event as ThreadEvent, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import uvicorn

from client_code.backend.main import create_app
from client_code.backend.services.storage_service import PROJECT_ROOT


EVENTS = '/api/v1/events'
STORAGE = '/api/v1/settings/storage'
AUTH = '/api/v1/auth'
ACCOUNT = {'username': 'admin', 'password': 'test-pass'}
WHEN = '2026-11-19T03:00:00.000000Z'


def login(client):
    response = client.post(AUTH + '/login', json=ACCOUNT)
    tokens = response.json()
    client.headers['Authorization'] = 'Bearer ' + tokens['access_token']
    return tokens


def seed(client):
    client.post(AUTH + '/signup', json=ACCOUNT | {'password_confirm': ACCOUNT['password']})
    tokens = login(client)
    client.post('/api/v1/cameras', json={'name': 'test', 'camera_type': 'webcam', 'webcam_index': 0})
    client.post('/api/v1/global-persons', json={'request_id': str(uuid4()),
        'feature_vector': [1.0] + [0.0] * 511, 'observed_at': WHEN})
    return tokens


def appeared(**changes):
    return dict(event_type='person_appeared', camera_id=1, camera_execution_id=str(uuid4()),
                tracking_generation=0, local_person_id=1, global_person_id=1, occurred_at=WHEN) | changes


def media(folder, name):
    return f'이벤트/person_1/2026-11-19/camera_1/{folder}/{name}'


def write_media(root, folder='관측'):
    for name in ('crop.jpg', 'clip.mp4', 'trajectory.jpg'):
        target = root / media(folder, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 백엔드는 디코더가 아니다. 파일 완료/이동 책임은 PC에 있으며 실재/경계만 검사한다.
        target.write_bytes(b'completed-test-placeholder')


class EventAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db_path = self.base / 'api.db'
        self.config = self.base / 'storage.json'
        self.root = self.base / 'selected' / 'AI_CCTV'
        self.other = self.base / 'other' / 'AI_CCTV'
        self.root.mkdir(parents=True)
        self.other.mkdir(parents=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.open_app()
        self.tokens = seed(self.client)

    def open_app(self):
        self.app = create_app(self.db_path, self.config)
        self.client = self.stack.enter_context(TestClient(self.app, raise_server_exceptions=False))

    def configure(self, root=None):
        response = self.client.put(STORAGE, json={'storage_root': str(root or self.root)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def count(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            return db.execute('SELECT count(*) FROM events').fetchone()[0]

    def test_all_six_routes_require_access_and_logout_revokes(self):
        requests = [('GET', EVENTS, None), ('POST', EVENTS, appeared()), ('GET', EVENTS + '/1', None),
                    ('PATCH', EVENTS + '/1/media', {'crop_path': None}), ('GET', STORAGE, None),
                    ('PUT', STORAGE, {'storage_root': str(self.root)})]
        for token in (None, self.tokens['refresh_token']):
            self.client.headers.pop('Authorization', None)
            if token:
                self.client.headers['Authorization'] = 'Bearer ' + token
            for method, path, body in requests:
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.json()['error']['code'], 'INVALID_ACCESS_TOKEN')
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        self.client.post(AUTH + '/logout', json={'refresh_token': self.tokens['refresh_token']})
        self.assertEqual(self.client.get(EVENTS).status_code, 401)
        self.assertEqual(self.client.get(STORAGE).status_code, 401)
        self.assertFalse(self.config.exists())

    def test_setting_required_even_for_camera_event(self):
        self.assertEqual(self.client.get(STORAGE).json(), {'storage_root': None, 'locked': False})
        body = {'event_type': 'camera_started', 'camera_id': 1, 'camera_execution_id': str(uuid4()), 'occurred_at': WHEN}
        response = self.client.post(EVENTS, json=body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'STORAGE_ROOT_NOT_CONFIGURED')
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.client.get(EVENTS).json(), {'items': []})
        self.assertFalse(self.config.exists())

    def test_expired_access_on_all_six_routes(self):
        service = self.app.state.auth_service
        with patch.object(service, 'clock', return_value=service.clock() + 901):
            for method, path, body in (
                    ('GET', EVENTS, None), ('POST', EVENTS, appeared()), ('GET', EVENTS + '/1', None),
                    ('PATCH', EVENTS + '/1/media', {'crop_path': None}), ('GET', STORAGE, None),
                    ('PUT', STORAGE, {'storage_root': str(self.root)})):
                self.assertEqual(self.client.request(method, path, json=body).status_code, 401)
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.config.exists())

    def test_four_types_media_rename_and_lost_create_response(self):
        self.configure()
        write_media(self.root)
        execution = str(uuid4())
        camera = dict(camera_id=1, camera_execution_id=execution, occurred_at=WHEN)
        self.assertEqual(self.client.post(EVENTS, json=camera | {'event_type': 'camera_started'}).status_code, 201)
        body = appeared(camera_execution_id=execution, crop_path=media('관측', 'crop.jpg'))
        response = self.client.post(EVENTS, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        first = response.json()
        self.assertIn('server_run_id', first)
        self.assertIsNone(first['clip_path'])
        target = EVENTS + f"/{first['event_id']}"
        self.assertEqual(response.headers['Location'], target)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        patch_paths = {field + '_path': media('관측', name) for field, name in
                       (('crop', 'crop.jpg'), ('clip', 'clip.mp4'), ('trajectory', 'trajectory.jpg'))}
        self.assertEqual(self.client.patch(target + '/media', json=patch_paths).status_code, 200)
        old = (self.root / patch_paths['crop_path']).parent
        new = old.with_name('관측~마지막')
        os.rename(old, new)
        final_paths = {k: v.replace('/관측/', '/관측~마지막/') for k, v in patch_paths.items()}
        updated = self.client.patch(target + '/media', json=final_paths)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.client.patch(target + '/media', json=final_paths).json(), updated.json())
        # 최초 응답을 잃고 옛 폴더 경로를 재전송해도 현재 미디어를 유지한다.
        duplicate = self.client.post(EVENTS, json=body)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json(), updated.json())
        gone = body | {'event_type': 'person_disappeared', 'related_event_id': first['event_id'],
                       'occurred_at': '2026-11-19T03:00:05Z', 'crop_path': None}
        disappeared = self.client.post(EVENTS, json=gone)
        self.assertEqual(disappeared.status_code, 201, disappeared.text)
        self.assertIsNone(disappeared.json()['crop_path'])
        self.assertEqual(self.client.post(EVENTS, json=gone).status_code, 200)
        stopped = camera | {'event_type': 'camera_stopped', 'occurred_at': '2026-11-19T03:00:06Z'}
        self.assertEqual(self.client.post(EVENTS, json=stopped).status_code, 201)
        self.assertEqual(self.count(), 4)
        detail = self.client.get(target)
        self.assertEqual(detail.json(), updated.json())
        self.assertNotIn(str(self.root), detail.text)
        with closing(sqlite3.connect(self.db_path)) as db:
            paths = db.execute('SELECT crop_path,clip_path,trajectory_path FROM events WHERE event_id=?',
                               (first['event_id'],)).fetchone()
        for path in paths:
            self.assertTrue((self.root / path).is_file())

    def test_invalid_inputs_bounds_and_no_raw_error_leak(self):
        self.configure()
        for changes in ({'camera_id': True}, {'camera_id': 2**63}, {'tracking_generation': 2**63},
                        {'occurred_at': '2026-11-19T03:00:00'}, {'camera_execution_id': 'secret-value'},
                        {'server_run_id': 1}, {'event_type': 'connection_error'}, {'message': 'secret-value'}):
            response = self.client.post(EVENTS, json=appeared(**changes))
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn('secret-value', response.text)
        for path in ('0', '-1', '1.5', '9223372036854775808', 'abc'):
            self.assertEqual(self.client.get(EVENTS + '/' + path).status_code, 422)
        for query in ({'limit': '0'}, {'limit': '101'}, {'limit': 'true'}, {'limit': '1.0'},
                      {'limit': ' 1'}, {'limit': '9' * 100}, {'before_event_id': '1'},
                      {'before_occurred_at': WHEN}, {'unexpected': 'secret-value'},
                      {'before_occurred_at': WHEN, 'before_event_id': '1.0'}):
            response = self.client.get(EVENTS, params=query)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn('secret-value', response.text)
        self.assertEqual(self.count(), 0)

    def test_list_cursor_order_and_missing_resources(self):
        self.configure()
        execution = str(uuid4())
        ids = [self.client.post(EVENTS, json=appeared(local_person_id=i, camera_execution_id=execution)).json()['event_id']
               for i in range(1, 5)]
        page = self.client.get(EVENTS, params={'limit': 2}).json()['items']
        self.assertEqual([r['event_id'] for r in page], ids[::-1][:2])
        last = page[-1]
        next_page = self.client.get(EVENTS, params={'limit': 2, 'before_event_id': last['event_id'],
            'before_occurred_at': '2026-11-19T12:00:00+09:00'}).json()['items']
        self.assertEqual([r['event_id'] for r in next_page], ids[::-1][2:])
        for method, path, body in [('GET', EVENTS + '/999', None), ('PATCH', EVENTS + '/999/media', {'crop_path': None})]:
            response = self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()['error']['code'], 'EVENT_NOT_FOUND')
        self.assertEqual(self.client.post(EVENTS, json=appeared(camera_id=999)).json()['error']['code'], 'CAMERA_NOT_FOUND')
        self.assertEqual(self.client.post(EVENTS, json=appeared(global_person_id=999)).json()['error']['code'], 'GLOBAL_PERSON_NOT_FOUND')

    def test_related_and_immutable_conflicts(self):
        self.configure()
        body = appeared()
        first = self.client.post(EVENTS, json=body).json()
        for changes in ({'occurred_at': '2026-11-19T03:00:01Z'}, {'global_person_id': 999}):
            response = self.client.post(EVENTS, json=body | changes)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error']['code'], 'EVENT_REQUEST_CONFLICT')
        gone = body | {'event_type': 'person_disappeared', 'related_event_id': first['event_id']}
        response = self.client.post(EVENTS, json=gone | {'local_person_id': 2})
        self.assertEqual(response.json()['error']['code'], 'EVENT_RELATION_CONFLICT')
        gone_response = self.client.post(EVENTS, json=gone)
        self.assertEqual(gone_response.status_code, 201)
        response = self.client.patch(EVENTS + f"/{gone_response.json()['event_id']}/media", json={'crop_path': None})
        self.assertEqual(response.json()['error']['code'], 'EVENT_MEDIA_NOT_ALLOWED')
        self.assertEqual(self.count(), 2)

    def test_path_syntax_layout_and_actual_file_validation(self):
        self.configure()
        write_media(self.root)
        zero = self.root / media('zero', 'crop.jpg')
        zero.parent.mkdir(parents=True)
        zero.touch()
        directory = self.root / media('directory', 'crop.jpg')
        directory.mkdir(parents=True)
        values = ['../crop.jpg', str(self.root / media('관측', 'crop.jpg')), 'C:crop.jpg',
                  'a\\b', 'a/CON.jpg', 'a/LPT1.txt', media('관측', 'crop.pending'),
                  media('missing', 'crop.jpg'), media('zero', 'crop.jpg'), media('directory', 'crop.jpg'),
                  media('관측', 'crop.jpg').replace('person_1', 'person_2'),
                  media('관측', 'crop.jpg').replace('camera_1', 'camera_2'),
                  media('관측', 'crop.jpg').replace('2026-11-19', '2026-02-30'),
                  'temp/crop.jpg', media('관측', 'clip.mp4')]
        for value in values:
            with self.subTest(path=value):
                response = self.client.post(EVENTS, json=appeared(crop_path=value))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn(str(self.root), response.text)
        self.assertEqual(self.count(), 0)

    def test_media_merge_null_and_atomic_rename_failure(self):
        self.configure()
        write_media(self.root, 'old')
        write_media(self.root, 'new')
        first = self.client.post(EVENTS, json=appeared(crop_path=media('old', 'crop.jpg'), clip_path=media('old', 'clip.mp4'))).json()
        target = EVENTS + f"/{first['event_id']}/media"
        # 폴더 변경 때는 모든 현재 경로를 보내며 이전/새 폴더를 혼합하지 않는다.
        response = self.client.patch(target, json={'crop_path': media('new', 'crop.jpg')})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(EVENTS + f"/{first['event_id']}").json(), first)
        response = self.client.patch(target, json={'crop_path': media('new', 'crop.jpg'), 'clip_path': media('missing', 'clip.mp4')})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(EVENTS + f"/{first['event_id']}").json(), first)
        updated = self.client.patch(target, json={'crop_path': None})
        self.assertEqual(updated.status_code, 200)
        self.assertIsNone(updated.json()['crop_path'])
        self.assertEqual(updated.json()['clip_path'], first['clip_path'])
        self.assertEqual(self.client.patch(target, json={}).status_code, 422)

    def test_directory_links_inside_and_outside_root_are_rejected(self):
        self.configure()
        for index, target in enumerate((self.root / 'elsewhere', self.base / 'outside')):
            target.mkdir()
            (target / 'crop.jpg').write_bytes(b'placeholder')
            link = self.root / media(f'link{index}', 'crop.jpg')
            link.parent.parent.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                subprocess.run(['cmd', '/c', 'mklink', '/J', str(link.parent), str(target)],
                               capture_output=True, check=True)
            else:
                link.parent.symlink_to(target, target_is_directory=True)
            response = self.client.post(EVENTS, json=appeared(crop_path=media(f'link{index}', 'crop.jpg')))
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.count(), 0)

    def test_root_validation_change_before_events_and_lock_after(self):
        for value in (None, 123, '', 'relative/AI_CCTV', str(self.base), str(self.base / 'missing' / 'AI_CCTV')):
            response = self.client.put(STORAGE, json={'storage_root': value})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.put(STORAGE, json={'storage_root': str(self.root), 'extra': 1}).status_code, 422)
        self.configure()
        self.configure(self.other)
        self.assertFalse(self.client.get(STORAGE).json()['locked'])
        self.assertEqual(self.client.post(EVENTS, json=appeared()).status_code, 201)
        original = self.config.read_bytes()
        response = self.client.put(STORAGE, json={'storage_root': str(self.root)})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'STORAGE_ROOT_LOCKED')
        self.assertEqual(self.config.read_bytes(), original)
        same = self.configure(self.other / '..' / 'AI_CCTV')
        self.assertTrue(same['locked'])

    def test_atomic_config_failure_keeps_old_file_and_memory(self):
        self.configure()
        previous = self.config.read_bytes()
        with patch('client_code.backend.services.storage_service.os.replace', side_effect=OSError('sensitive internal path')):
            response = self.client.put(STORAGE, json={'storage_root': str(self.other)})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('sensitive', response.text)
        self.assertEqual(self.config.read_bytes(), previous)
        self.assertEqual(self.client.get(STORAGE).json()['storage_root'], str(self.root))
        self.assertEqual(list(self.base.glob('storage.json.*.pending')), [])
        self.configure(self.other)

    def test_temporary_output_and_other_volume_roots_are_rejected(self):
        with patch('client_code.backend.services.storage_service.PROJECT_ROOT', self.base):
            for name in ('temp', 'output'):
                root = self.base / name / 'AI_CCTV'
                root.mkdir(parents=True)
                self.assertEqual(self.client.put(STORAGE, json={'storage_root': str(root)}).status_code, 422)
        original_stat = Path.stat
        def other_volume(path, *args, **kwargs):
            return SimpleNamespace(st_dev=-1) if path == PROJECT_ROOT else original_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', other_volume):
            self.assertEqual(self.client.put(STORAGE, json={'storage_root': str(self.root)}).status_code, 422)
        self.assertFalse(self.config.exists())

    def test_unavailable_root_keeps_history_readable_and_rejects_new_writes(self):
        self.configure()
        saved = self.client.post(EVENTS, json=appeared()).json()
        os.rename(self.root, self.root.with_name('temporarily-unavailable'))
        self.assertEqual(self.client.get(EVENTS).status_code, 200)
        self.assertEqual(self.client.get(EVENTS + f"/{saved['event_id']}").json(), saved)
        self.assertEqual(self.client.get(STORAGE).json(), {'storage_root': str(self.root), 'locked': True})
        self.assertEqual(self.client.post(EVENTS, json=appeared()).status_code, 422)
        self.assertEqual(self.count(), 1)

    def test_failed_event_does_not_lock_settings_and_errors_are_sanitized(self):
        self.configure()
        with patch('client_code.backend.services.event_service.EventResponse.model_validate', side_effect=RuntimeError('sensitive')):
            response = self.client.post(EVENTS, json=appeared())
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('sensitive', response.text)
        self.assertEqual(self.count(), 0)
        self.configure(self.other)
        with patch.object(self.app.state.event_service.repository, 'list', side_effect=RuntimeError('sensitive')):
            response = self.client.get(EVENTS)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('sensitive', response.text)

    def test_concurrent_duplicate_posts(self):
        self.configure()
        body = appeared()
        barrier = Barrier(4)
        def send(_):
            barrier.wait()
            return self.client.post(EVENTS, json=body)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(send, range(4)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 200, 200, 201])
        self.assertEqual(len({r.json()['event_id'] for r in results}), 1)
        self.assertEqual(self.count(), 1)

    def test_setting_change_cannot_pass_inflight_event_commit(self):
        self.configure()
        write_media(self.root)
        checked, release = ThreadEvent(), ThreadEvent()
        validate = self.app.state.storage_service.validate_media
        def hold(values):
            validate(values)
            checked.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
        with patch.object(self.app.state.storage_service, 'validate_media', side_effect=hold):
            with ThreadPoolExecutor(max_workers=2) as pool:
                created = pool.submit(self.client.post, EVENTS, json=appeared(crop_path=media('관측', 'crop.jpg')))
                try:
                    self.assertTrue(checked.wait(5))
                    changed = pool.submit(self.client.put, STORAGE, json={'storage_root': str(self.other)})
                finally:
                    release.set()
                self.assertEqual(created.result(timeout=5).status_code, 201)
                self.assertEqual(changed.result(timeout=5).status_code, 409)
        self.assertEqual(self.client.get(STORAGE).json(), {'storage_root': str(self.root), 'locked': True})

    def test_restart_retains_config_history_lock_and_rejects_old_token(self):
        self.configure()
        saved = self.client.post(EVENTS, json=appeared()).json()
        self.stack.close()
        self.open_app()
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        self.assertEqual(self.client.get(EVENTS).status_code, 401)
        login(self.client)
        self.assertEqual(self.client.get(STORAGE).json(), {'storage_root': str(self.root), 'locked': True})
        self.assertEqual(self.client.get(EVENTS + f"/{saved['event_id']}").json(), saved)
        self.assertEqual(self.client.put(STORAGE, json={'storage_root': str(self.other)}).status_code, 409)
        self.configure()

    def test_missing_or_corrupt_config_is_not_silently_reset(self):
        self.configure()
        self.client.post(EVENTS, json=appeared())
        self.stack.close()
        self.config.unlink()
        self.open_app()
        login(self.client)
        self.assertEqual(self.client.get(STORAGE).json(), {'storage_root': None, 'locked': True})
        self.assertEqual(self.client.put(STORAGE, json={'storage_root': str(self.other)}).status_code, 409)
        self.assertEqual(self.client.post(EVENTS, json=appeared()).status_code, 409)
        self.stack.close()
        self.config.write_text('{invalid private data', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '저장소 설정 파일'):
            with TestClient(create_app(self.db_path, self.config)):
                pass
        self.assertEqual(self.config.read_text(encoding='utf-8'), '{invalid private data')


class EventHTTPTests(unittest.TestCase):
    def test_real_uvicorn_http(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'selected' / 'AI_CCTV'
            root.mkdir(parents=True)
            write_media(root)
            app = create_app(base / 'http.db', base / 'http.storage.json')
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='critical'))
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(server.started)
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5) as client:
                    self.assertEqual(client.get(EVENTS).status_code, 401)
                    seed(client)
                    self.assertEqual(client.put(STORAGE, json={'storage_root': str(root)}).status_code, 200)
                    body = appeared(crop_path=media('관측', 'crop.jpg'))
                    first = client.post(EVENTS, json=body)
                    self.assertEqual(first.status_code, 201, first.text)
                    retry = client.post(EVENTS, json=body)
                    self.assertEqual(retry.status_code, 200)
                    self.assertEqual(first.json()['event_id'], retry.json()['event_id'])
                    target = EVENTS + f"/{first.json()['event_id']}"
                    patched = client.patch(target + '/media', json={'clip_path': media('관측', 'clip.mp4')})
                    self.assertEqual(patched.status_code, 200, patched.text)
                    self.assertEqual(client.get(target).json(), patched.json())
                    self.assertEqual(client.get(EVENTS, params={'limit': 1}).json()['items'], [patched.json()])
                    self.assertEqual(client.get(STORAGE).json(), {'storage_root': str(root), 'locked': True})
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                sock.close()
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
