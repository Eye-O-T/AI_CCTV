"""모바일 목록·crop 검증. 운영 DB/계정/미디어는 사용하지 않는다."""
from contextlib import closing
from io import BytesIO
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
from threading import Thread
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import uvicorn
from PIL import Image

from client_code.backend.main import create_app
from client_code.backend.tests.test_cnn_result import RESULT
from client_code.backend.tests.test_event_api import seed, appeared, write_media, media, WHEN


MOBILE = '/api/v1/mobile/events'
EVENTS = '/api/v1/events'
DATE = '2026-11-19'


def jpeg_bytes():
    output = BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(output, format='JPEG')
    return output.getvalue()


class MobileEventAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'AI_CCTV'
        self.root.mkdir()
        self.app = create_app(self.base / 'test.db')
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

    def page(self, **params):
        response = self.client.get(MOBILE, params={'date': DATE} | params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.json()

    def create_crop(self):
        write_media(self.root)
        path = self.root / media('관측', 'crop.jpg')
        path.write_bytes(jpeg_bytes())
        body = appeared(crop_path=media('관측', 'crop.jpg'))
        event = self.create(body)
        return body, event, path, MOBILE + f"/{event['event_id']}/crop"

    def assert_crop_missing(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()['error']['code'], 'EVENT_CROP_NOT_FOUND')
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn('crop.jpg', response.text)

    def test_crop_appearance_disappearance_bytes_and_no_db_changes(self):
        body, event, path, url = self.create_crop()
        gone = self.create(body | {'event_type': 'person_disappeared', 'related_event_id': event['event_id'],
            'crop_path': None, 'occurred_at': '2026-11-19T03:00:05Z'})
        for target in (url, MOBILE + f"/{gone['event_id']}/crop"):
            response = self.client.get(target)
            self.assertEqual(response.status_code, 200, response.text[:100])
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.content, path.read_bytes())
            with Image.open(BytesIO(response.content)) as image:
                self.assertEqual(image.size, (2, 2))
        self.assertEqual(self.client.get(EVENTS + f"/{event['event_id']}").json(), event)
        self.assertEqual(self.client.get(EVENTS + f"/{gone['event_id']}").json(), gone)
        self.assertEqual(len(self.page()['items']), 2)

    def test_crop_missing_event_no_path_camera_and_invalid_id(self):
        self.assertEqual(self.client.get(MOBILE + '/999/crop').json()['error']['code'], 'EVENT_NOT_FOUND')
        person = self.create(appeared())
        self.assert_crop_missing(MOBILE + f"/{person['event_id']}/crop")
        for kind in ('camera_started', 'camera_stopped'):
            event = self.create(dict(event_type=kind, camera_id=1, camera_execution_id=str(uuid4()), occurred_at=WHEN))
            self.assert_crop_missing(MOBILE + f"/{event['event_id']}/crop")
        for event_id in ('0', '-1', 'abc', '9223372036854775808'):
            self.assertEqual(self.client.get(MOBILE + f'/{event_id}/crop').status_code, 422)

    def test_crop_missing_empty_directory_and_storage_unavailable(self):
        _, _, path, url = self.create_crop()
        path.unlink()
        self.assert_crop_missing(url)
        path.touch()
        self.assert_crop_missing(url)
        path.unlink()
        path.mkdir()
        self.assert_crop_missing(url)
        path.rmdir()
        path.write_bytes(jpeg_bytes())
        for root in (None, self.base / 'missing' / 'AI_CCTV'):
            with patch.object(self.app.state.storage_service, 'root', root):
                self.assert_crop_missing(url)
                self.assertEqual(len(self.page()['items']), 1)
        # 다른 미디어가 소실되어도 crop만 읽을 수 있다.
        path.with_name('clip.mp4').unlink()
        path.with_name('trajectory.jpg').unlink()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_crop_rejects_tampered_paths_and_read_failure(self):
        _, event, path, url = self.create_crop()
        for value in ('../outside.jpg', str(path), 'C:/outside.jpg',
                      media('관측', 'clip.mp4'), media('관측', 'crop.jpg').replace('person_1', 'person_2')):
            with closing(sqlite3.connect(self.base / 'test.db')) as db:
                db.execute('UPDATE events SET crop_path=? WHERE event_id=?', (value, event['event_id']))
                db.commit()
            self.assert_crop_missing(url)
        with closing(sqlite3.connect(self.base / 'test.db')) as db:
            db.execute('UPDATE events SET crop_path=? WHERE event_id=?', (media('관측', 'crop.jpg'), event['event_id']))
            db.commit()
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('private internal path')):
            self.assert_crop_missing(url)

    def test_crop_rejects_directory_link_even_inside_storage(self):
        _, _, path, url = self.create_crop()
        folder = path.parent
        target = folder.with_name('real-observation')
        folder.rename(target)
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(folder), str(target)],
                           capture_output=True, check=True)
        else:
            folder.symlink_to(target, target_is_directory=True)
        try:
            self.assert_crop_missing(url)
        finally:
            if os.name == 'nt':
                os.rmdir(folder)  # 제거하는 것은 임시 junction 자체뿐이다.
            else:
                folder.unlink()

    def test_crop_same_url_uses_latest_registered_folder(self):
        _, event, path, url = self.create_crop()
        path.parent.rename(path.parent.with_name('관측~마지막'))
        self.assert_crop_missing(url)
        response = self.client.patch(EVENTS + f"/{event['event_id']}/media",
            json={'crop_path': media('관측~마지막', 'crop.jpg')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(url).content, jpeg_bytes())
        self.assertEqual(self.page()['items'][0]['crop_url'], url)

    def test_crop_auth_missing_refresh_expired_logout(self):
        _, _, _, url = self.create_crop()
        for token in (None, self.tokens['refresh_token']):
            self.client.headers.pop('Authorization', None)
            if token:
                self.client.headers['Authorization'] = 'Bearer ' + token
            self.assertEqual(self.client.get(url).status_code, 401)
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        auth = self.app.state.auth_service
        with patch.object(auth, 'clock', return_value=auth.clock() + 901):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.client.post('/api/v1/auth/logout', json={'refresh_token': self.tokens['refresh_token']})
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_korean_date_boundaries_and_empty_day(self):
        times = ['2026-11-18T14:59:59.999999Z', '2026-11-18T15:00:00Z',
                 '2026-11-19T14:59:59.999999Z', '2026-11-19T15:00:00Z']
        ids = [self.create(appeared(occurred_at=value))['event_id'] for value in times]
        self.assertEqual([r['event_id'] for r in self.page()['items']], [ids[2], ids[1]])
        self.assertEqual(self.page(date='2026-11-21'),
                         {'date': '2026-11-21', 'items': [], 'next_cursor': None})
        self.assertEqual(self.page(date='9999-12-31')['items'], [])

    def test_four_cards_related_appearance_and_cnn_refresh(self):
        write_media(self.root)
        body = appeared(crop_path=media('관측', 'crop.jpg'))
        person = self.create(body)
        gone = self.create(body | {'event_type': 'person_disappeared',
            'related_event_id': person['event_id'], 'crop_path': None,
            'occurred_at': '2026-11-19T03:00:05Z'})
        execution = str(uuid4())
        for kind in ('camera_started', 'camera_stopped'):
            self.create(dict(event_type=kind, camera_id=1, camera_execution_id=execution, occurred_at=WHEN))
        initial = self.page()['items']
        self.assertEqual(len(initial), 4)
        for row in initial:
            self.assertEqual(row['camera_name'], 'test')
            self.assertIsNone(row['estimated_gender'])
            self.assertIsNone(row['estimated_age'])
            if row['event_type'].startswith('camera_'):
                self.assertIsNone(row['global_person_id'])
                self.assertIsNone(row['crop_url'])
            else:
                self.assertEqual(row['crop_url'], MOBILE + f"/{row['event_id']}/crop")
        self.assertEqual(self.client.patch(EVENTS + f"/{person['event_id']}/cnn-result",
                         json={'cnn_result': RESULT}).status_code, 200)
        refreshed = {r['event_id']: r for r in self.page()['items']}
        for event in (person, gone):
            row = refreshed[event['event_id']]
            self.assertEqual(row['estimated_gender'], 'Male')
            self.assertEqual(row['estimated_age'], '청년')
            self.assertEqual(row['event_type'], event['event_type'])
            self.assertEqual(row['occurred_at'], event['occurred_at'])
        # 기존 상세 DTO와 사라짐 원본은 그대로 유지한다.
        self.assertEqual(self.client.get(EVENTS + f"/{gone['event_id']}").json(), gone)
        self.assertNotIn(str(self.root), str(refreshed))
        self.assertNotIn('crop_path', str(refreshed))
        self.assertEqual(self.client.get(MOBILE + f"/{person['event_id']}/crop").status_code, 200)

    def test_media_refresh_and_current_camera_name_without_storage(self):
        event = self.create(appeared())
        self.assertIsNone(self.page()['items'][0]['crop_url'])
        write_media(self.root)
        self.assertEqual(self.client.patch(EVENTS + f"/{event['event_id']}/media",
                         json={'crop_path': media('관측', 'crop.jpg')}).status_code, 200)
        self.assertEqual(self.client.patch('/api/v1/cameras/1', json={'name': '정문'}).status_code, 200)
        with patch.object(self.app.state.storage_service, 'root', None):
            row = self.page()['items'][0]
        self.assertEqual(row['camera_name'], '정문')
        self.assertIsNotNone(row['crop_url'])

    def test_pagination_ties_and_date_filter_on_every_page(self):
        self.create(appeared(occurred_at='2026-11-18T03:00:00Z'))
        ids = [self.create(appeared())['event_id'] for _ in range(5)]
        received = []
        cursor = {}
        while True:
            page = self.page(limit=2, **cursor)
            received.extend(row['event_id'] for row in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(received, ids[::-1])
        # 동일 커서의 시간대 표현이 달라도 같은 결과여야 한다.
        page = self.page(limit=2)
        cursor = page['next_cursor'] | {'before_occurred_at': '2026-11-19T12:00:00+09:00'}
        self.assertEqual([r['event_id'] for r in self.page(limit=2, **cursor)['items']], ids[::-1][2:4])

    def test_default_limit_and_exact_last_page(self):
        for _ in range(31):
            self.create(appeared())
        self.assertEqual(len(self.page()['items']), 30)
        exact = self.page(limit=31)
        self.assertEqual(len(exact['items']), 31)
        self.assertIsNone(exact['next_cursor'])

    def test_input_validation_and_no_raw_leak(self):
        bad = [{}, {'date': '2026-02-30'}, {'date': '20261119'},
               {'date': '2026-11-19T00:00:00Z'}, {'date': '0001-01-01'},
               {'date': DATE, 'limit': '0'},
               {'date': DATE, 'limit': '101'}, {'date': DATE, 'limit': '1.0'},
               {'date': DATE, 'limit': 'true'}, {'date': DATE, 'before_event_id': '1'},
               {'date': DATE, 'before_occurred_at': WHEN},
               {'date': DATE, 'before_event_id': '1', 'before_occurred_at': '2026-11-19T00:00:00'},
               {'date': DATE, 'unexpected': 'secret-value'}]
        for params in bad:
            with self.subTest(params=params):
                response = self.client.get(MOBILE, params=params)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn('secret-value', response.text)

    def test_auth_missing_refresh_expired_and_logout(self):
        for token in (None, self.tokens['refresh_token']):
            self.client.headers.pop('Authorization', None)
            if token:
                self.client.headers['Authorization'] = 'Bearer ' + token
            self.assertEqual(self.client.get(MOBILE, params={'date': DATE}).status_code, 401)
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        auth = self.app.state.auth_service
        with patch.object(auth, 'clock', return_value=auth.clock() + 901):
            self.assertEqual(self.client.get(MOBILE, params={'date': DATE}).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/logout',
                         json={'refresh_token': self.tokens['refresh_token']}).status_code, 204)
        self.assertEqual(self.client.get(MOBILE, params={'date': DATE}).status_code, 401)


class MobileEventHTTPTests(unittest.TestCase):
    def test_real_http_list_and_cnn_refresh(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'AI_CCTV'
            root.mkdir()
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(create_app(base / 'http.db'), log_level='critical'))
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(server.started)
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5) as client:
                    self.assertEqual(client.get(MOBILE, params={'date': DATE}).status_code, 401)
                    seed(client)
                    self.assertEqual(client.put('/api/v1/settings/storage',
                                     json={'storage_root': str(root)}).status_code, 200)
                    write_media(root)
                    (root / media('관측', 'crop.jpg')).write_bytes(jpeg_bytes())
                    body = appeared(crop_path=media('관측', 'crop.jpg'))
                    event = client.post(EVENTS, json=body).json()
                    response = client.get(MOBILE, params={'date': DATE})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertEqual(response.json()['items'][0]['event_id'], event['event_id'])
                    self.assertIsNone(response.json()['items'][0]['estimated_age'])
                    self.assertEqual(client.patch(EVENTS + f"/{event['event_id']}/cnn-result",
                                     json={'cnn_result': RESULT}).status_code, 200)
                    self.assertEqual(client.get(MOBILE, params={'date': DATE}).json()
                                     ['items'][0]['estimated_age'], '청년')
                    self.assertEqual(client.get(MOBILE, params={'date': 'invalid'}).status_code, 422)
                    crop_url = response.json()['items'][0]['crop_url']
                    crop = client.get(crop_url)
                    self.assertEqual(crop.status_code, 200)
                    self.assertEqual(crop.headers['Content-Type'], 'image/jpeg')
                    self.assertEqual(crop.headers['Cache-Control'], 'no-store')
                    self.assertEqual(crop.content, jpeg_bytes())
                    gone = client.post(EVENTS, json=body | {'event_type': 'person_disappeared',
                        'related_event_id': event['event_id'], 'crop_path': None,
                        'occurred_at': '2026-11-19T03:00:05Z'})
                    self.assertEqual(gone.status_code, 201, gone.text)
                    self.assertEqual(client.get(MOBILE + f"/{gone.json()['event_id']}/crop").content, crop.content)
                    (root / media('관측', 'crop.jpg')).unlink()
                    missing = client.get(crop_url)
                    self.assertEqual(missing.status_code, 404)
                    self.assertEqual(missing.json()['error']['code'], 'EVENT_CROP_NOT_FOUND')
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                sock.close()
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
