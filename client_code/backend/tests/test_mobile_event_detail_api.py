"""모바일 인물 상세 JSON 검증. 운영 DB/파일/카메라는 사용하지 않는다."""
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import uvicorn

from client_code.backend.main import create_app
from client_code.backend.tests.test_cnn_result import RESULT
from client_code.backend.tests.test_event_api import seed, appeared, write_media, media, WHEN
from client_code.backend.tests.test_mobile_event_api import jpeg_bytes


EVENTS = '/api/v1/events'
MOBILE = '/api/v1/mobile/events'


class MobileEventDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'AI_CCTV'
        self.root.mkdir()
        self.app = create_app(Path(self.tmp.name) / 'test.db')
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.tokens = seed(self.client)
        self.assertEqual(self.client.put('/api/v1/settings/storage',
                         json={'storage_root': str(self.root)}).status_code, 200)

    def create(self, body):
        response = self.client.post(EVENTS, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def detail(self, event_id):
        response = self.client.get(MOBILE + f'/{event_id}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.json()

    def pair(self):
        body = appeared()
        person = self.create(body)
        gone = self.create(body | {'event_type': 'person_disappeared',
            'related_event_id': person['event_id'], 'occurred_at': '2026-11-19T03:00:05Z'})
        return person, gone

    def trajectory_pair(self):
        person, gone = self.pair()
        write_media(self.root)
        path = self.root / media('관측', 'trajectory.jpg')
        path.write_bytes(jpeg_bytes())
        response = self.client.patch(EVENTS + f"/{person['event_id']}/media",
                                     json={'trajectory_path': media('관측', 'trajectory.jpg')})
        self.assertEqual(response.status_code, 200, response.text)
        return person, gone, path

    def assert_trajectory_missing(self, event_id):
        response = self.client.get(MOBILE + f'/{event_id}/trajectory')
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()['error']['code'], 'EVENT_TRAJECTORY_NOT_FOUND')
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn('trajectory.jpg', response.text)

    def test_trajectory_jpeg_for_both_types_without_other_media_or_db_changes(self):
        person, gone, path = self.trajectory_pair()
        path.with_name('crop.jpg').unlink()
        path.with_name('clip.mp4').unlink()
        before = self.client.get(EVENTS).json()
        for event in (person, gone):
            value = self.detail(event['event_id'])
            response = self.client.get(value['trajectory_url'])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.content, jpeg_bytes())
        self.assertEqual(self.client.get(EVENTS).json(), before)

    def test_trajectory_no_path_camera_missing_event_and_invalid_id(self):
        person = self.create(appeared())
        self.assert_trajectory_missing(person['event_id'])
        for kind in ('camera_started', 'camera_stopped'):
            event = self.create(dict(event_type=kind, camera_id=1,
                camera_execution_id=str(uuid4()), occurred_at=WHEN))
            self.assert_trajectory_missing(event['event_id'])
        response = self.client.get(MOBILE + '/9999/trajectory')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'EVENT_NOT_FOUND')
        for value in ('0', '-1', 'abc', '1.5', '9223372036854775808'):
            self.assertEqual(self.client.get(MOBILE + f'/{value}/trajectory').status_code, 422)

    def test_trajectory_missing_empty_directory_storage_and_read_failure(self):
        person, _, path = self.trajectory_pair()
        path.unlink()
        self.assert_trajectory_missing(person['event_id'])
        path.touch()
        self.assert_trajectory_missing(person['event_id'])
        path.unlink()
        path.mkdir()
        self.assert_trajectory_missing(person['event_id'])
        path.rmdir()
        path.write_bytes(jpeg_bytes())
        for root in (None, self.root.parent / 'missing' / 'AI_CCTV'):
            with patch.object(self.app.state.storage_service, 'root', root):
                self.assert_trajectory_missing(person['event_id'])
                self.assertIsNotNone(self.detail(person['event_id'])['trajectory_url'])
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('private internal path')):
            self.assert_trajectory_missing(person['event_id'])

    def test_trajectory_rejects_tampered_db_paths(self):
        person, _, path = self.trajectory_pair()
        for value in ('../outside.jpg', str(path), 'C:/outside.jpg',
                      media('관측', 'crop.jpg'), media('관측', 'trajectory.jpg').replace('camera_1', 'camera_2')):
            with closing(sqlite3.connect(Path(self.tmp.name) / 'test.db')) as db:
                db.execute('UPDATE events SET trajectory_path=? WHERE event_id=?', (value, person['event_id']))
                db.commit()
            self.assert_trajectory_missing(person['event_id'])

    def test_trajectory_rejects_junction_or_symlink(self):
        person, _, path = self.trajectory_pair()
        folder = path.parent
        target = folder.with_name('real-observation')
        folder.rename(target)
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(folder), str(target)], capture_output=True, check=True)
        else:
            folder.symlink_to(target, target_is_directory=True)
        try:
            self.assert_trajectory_missing(person['event_id'])
        finally:
            if os.name == 'nt':
                os.rmdir(folder)  # 임시 junction 자체만 제거한다.
            else:
                folder.unlink()

    def test_trajectory_same_url_after_registered_folder_change(self):
        person, gone, path = self.trajectory_pair()
        url = self.detail(gone['event_id'])['trajectory_url']
        path.parent.rename(path.parent.with_name('관측~마지막'))
        self.assert_trajectory_missing(gone['event_id'])
        self.assertEqual(self.client.patch(EVENTS + f"/{person['event_id']}/media",
                         json={'trajectory_path': media('관측~마지막', 'trajectory.jpg')}).status_code, 200)
        self.assertEqual(self.detail(gone['event_id'])['trajectory_url'], url)
        self.assertEqual(self.client.get(url).content, jpeg_bytes())

    def test_trajectory_auth_missing_refresh_expired_logout(self):
        person, _, _ = self.trajectory_pair()
        url = MOBILE + f"/{person['event_id']}/trajectory"
        for token in (None, self.tokens['refresh_token']):
            self.client.headers.pop('Authorization', None)
            if token:
                self.client.headers['Authorization'] = 'Bearer ' + token
            self.assertEqual(self.client.get(url).status_code, 401)
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        auth = self.app.state.auth_service
        with patch.object(auth, 'clock', return_value=auth.clock() + 901):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/logout',
                         json={'refresh_token': self.tokens['refresh_token']}).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_appearance_and_disappearance_nulls_and_original_identity(self):
        person, gone = self.pair()
        for event in (person, gone):
            value = self.detail(event['event_id'])
            self.assertEqual(set(value), {'event_id', 'event_type', 'occurred_at', 'global_person_id',
                'camera_id', 'camera_name', 'cnn_result', 'crop_url', 'trajectory_url', 'clip_url'})
            for key in ('event_id', 'event_type', 'occurred_at', 'global_person_id', 'camera_id'):
                self.assertEqual(value[key], event[key])
            self.assertEqual(value['camera_name'], 'test')
            for key in ('cnn_result', 'crop_url', 'trajectory_url', 'clip_url'):
                self.assertIsNone(value[key])

    def test_refresh_all_media_and_full_cnn_from_related_appearance(self):
        person, gone = self.pair()
        self.assertIsNone(self.detail(gone['event_id'])['cnn_result'])
        write_media(self.root)
        media_paths = {kind + '_path': media('관측', filename) for kind, filename in
                      (('crop', 'crop.jpg'), ('trajectory', 'trajectory.jpg'), ('clip', 'clip.mp4'))}
        self.assertEqual(self.client.patch(EVENTS + f"/{person['event_id']}/media", json=media_paths).status_code, 200)
        self.assertEqual(self.client.patch(EVENTS + f"/{person['event_id']}/cnn-result",
                         json={'cnn_result': RESULT}).status_code, 200)
        original = self.client.get(EVENTS).json()
        for event in (person, gone):
            value = self.detail(event['event_id'])
            self.assertEqual(value['cnn_result'], RESULT)
            for kind in ('crop', 'trajectory', 'clip'):
                self.assertEqual(value[kind + '_url'], MOBILE + f"/{event['event_id']}/{kind}")
            self.assertEqual(value['occurred_at'], event['occurred_at'])
            self.assertNotIn(str(self.root), str(value))
            self.assertNotIn('crop_path', value)
        self.assertEqual(self.client.get(EVENTS).json(), original)
        self.assertIsNone(self.client.get(EVENTS + f"/{gone['event_id']}").json()['cnn_result'])
        self.assertEqual(self.client.get(MOBILE + f"/{person['event_id']}/trajectory").status_code, 200)
        self.assertEqual(self.client.get(MOBILE + f"/{person['event_id']}/clip").status_code, 200)

    def test_independent_media_and_current_camera_name_without_storage(self):
        person = self.create(appeared())
        write_media(self.root)
        target = EVENTS + f"/{person['event_id']}/media"
        self.assertEqual(self.client.patch(target, json={'crop_path': media('관측', 'crop.jpg')}).status_code, 200)
        self.assertEqual(self.client.patch('/api/v1/cameras/1', json={'name': '정문'}).status_code, 200)
        (self.root / media('관측', 'crop.jpg')).unlink()
        with patch.object(self.app.state.storage_service, 'root', None):
            value = self.detail(person['event_id'])
        self.assertEqual(value['camera_name'], '정문')
        self.assertIsNotNone(value['crop_url'])
        self.assertIsNone(value['trajectory_url'])
        self.assertIsNone(value['clip_url'])
        self.assertEqual(self.client.patch(target, json={'crop_path': None}).status_code, 200)
        self.assertIsNone(self.detail(person['event_id'])['crop_url'])

    def test_missing_camera_types_and_invalid_ids(self):
        response = self.client.get(MOBILE + '/9999')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'EVENT_NOT_FOUND')
        for kind in ('camera_started', 'camera_stopped'):
            event = self.create(dict(event_type=kind, camera_id=1,
                camera_execution_id=str(uuid4()), occurred_at=WHEN))
            response = self.client.get(MOBILE + f"/{event['event_id']}")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error']['code'], 'EVENT_DETAIL_NOT_SUPPORTED')
        for event_id in ('0', '-1', '1.5', 'abc', '9223372036854775808'):
            self.assertEqual(self.client.get(MOBILE + '/' + event_id).status_code, 422)

    def test_auth_missing_refresh_expired_and_logout(self):
        person = self.create(appeared())
        url = MOBILE + f"/{person['event_id']}"
        for token in (None, self.tokens['refresh_token']):
            self.client.headers.pop('Authorization', None)
            if token:
                self.client.headers['Authorization'] = 'Bearer ' + token
            self.assertEqual(self.client.get(url).status_code, 401)
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        auth = self.app.state.auth_service
        with patch.object(auth, 'clock', return_value=auth.clock() + 901):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/logout',
                         json={'refresh_token': self.tokens['refresh_token']}).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 401)


class MobileEventDetailHTTPTests(unittest.TestCase):
    def test_real_http_detail_and_refresh(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'AI_CCTV'
            root.mkdir()
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(create_app(Path(tmp) / 'test.db'), log_level='critical'))
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(server.started)
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5) as client:
                    self.assertEqual(client.get(MOBILE + '/1').status_code, 401)
                    seed(client)
                    self.assertEqual(client.put('/api/v1/settings/storage',
                                     json={'storage_root': str(root)}).status_code, 200)
                    body = appeared()
                    person = client.post(EVENTS, json=body).json()
                    gone_response = client.post(EVENTS, json=body | {'event_type': 'person_disappeared',
                        'related_event_id': person['event_id'], 'occurred_at': '2026-11-19T03:00:05Z'})
                    self.assertEqual(gone_response.status_code, 201, gone_response.text)
                    gone = gone_response.json()
                    url = MOBILE + f"/{gone['event_id']}"
                    self.assertIsNone(client.get(url).json()['cnn_result'])
                    self.assertEqual(client.patch(EVENTS + f"/{person['event_id']}/cnn-result",
                                     json={'cnn_result': RESULT}).status_code, 200)
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertEqual(response.json()['event_type'], 'person_disappeared')
                    self.assertEqual(response.json()['occurred_at'], gone['occurred_at'])
                    self.assertEqual(response.json()['cnn_result'], RESULT)
                    write_media(root)
                    path = root / media('관측', 'trajectory.jpg')
                    path.write_bytes(jpeg_bytes())
                    self.assertEqual(client.patch(EVENTS + f"/{person['event_id']}/media",
                        json={'trajectory_path': media('관측', 'trajectory.jpg')}).status_code, 200)
                    for event in (person, gone):
                        trajectory_url = client.get(MOBILE + f"/{event['event_id']}").json()['trajectory_url']
                        image = client.get(trajectory_url)
                        self.assertEqual(image.status_code, 200)
                        self.assertEqual(image.headers['Content-Type'], 'image/jpeg')
                        self.assertEqual(image.content, jpeg_bytes())
                    path.unlink()
                    missing = client.get(trajectory_url)
                    self.assertEqual(missing.status_code, 404)
                    self.assertEqual(missing.json()['error']['code'], 'EVENT_TRAJECTORY_NOT_FOUND')
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                sock.close()
            self.assertFalse(thread.is_alive())
