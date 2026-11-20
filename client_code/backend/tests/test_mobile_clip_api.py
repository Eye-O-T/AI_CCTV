"""클립 바이트/Range/리소스 정리 검증. 운영 DB·미디어를 사용하지 않는다."""
import asyncio
from contextlib import closing
from io import BytesIO
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
from client_code.backend.routers.mobile_events import _ClipResponse
from client_code.backend.tests.test_event_api import seed, appeared, write_media, media, WHEN


EVENTS = '/api/v1/events'
MOBILE = '/api/v1/mobile/events'
DATA = bytes(range(256)) * 600  # 여러 청크의 바이트 검증용. 실제 MP4 디코딩은 아래 HTTP 테스트에서 확인한다.


class MobileClipTests(unittest.TestCase):
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
        write_media(self.root)
        self.path = self.root / media('관측', 'clip.mp4')
        self.path.write_bytes(DATA)
        self.body = appeared(clip_path=media('관측', 'clip.mp4'))
        self.event = self.create(self.body)
        self.url = MOBILE + f"/{self.event['event_id']}/clip"

    def create(self, body):
        response = self.client.post(EVENTS, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def assert_missing(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()['error']['code'], 'EVENT_CLIP_NOT_FOUND')
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn('clip.mp4', response.text)

    def test_full_and_disappearance_without_images_or_db_changes(self):
        self.path.with_name('crop.jpg').unlink()
        self.path.with_name('trajectory.jpg').unlink()
        gone = self.create(self.body | {'event_type': 'person_disappeared', 'clip_path': None,
            'related_event_id': self.event['event_id'], 'occurred_at': '2026-11-19T03:00:05Z'})
        original = self.client.get(EVENTS).json()
        for event in (self.event, gone):
            url = self.client.get(MOBILE + f"/{event['event_id']}").json()['clip_url']
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, DATA)
            self.assertEqual(response.headers['Content-Type'], 'video/mp4')
            self.assertEqual(response.headers['Content-Length'], str(len(DATA)))
            self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertNotIn('Content-Range', response.headers)
        self.assertEqual(self.client.get(EVENTS).json(), original)

    def test_range_closed_open_suffix_clamping_and_if_range(self):
        cases = [('bytes=0-0', 0, 0), ('bytes=5-19', 5, 19),
                 ('bytes=65000-66000', 65000, 66000), ('bytes=65536-', 65536, len(DATA)-1),
                 ('bytes=-7', len(DATA)-7, len(DATA)-1),
                 ('bytes=-9999999', 0, len(DATA)-1), ('bytes=10-9999999', 10, len(DATA)-1),
                 (f'bytes={len(DATA)-1}-', len(DATA)-1, len(DATA)-1)]
        for value, start, end in cases:
            with self.subTest(value=value):
                response = self.client.get(self.url, headers={'Range': value.encode('utf-8')})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, DATA[start:end+1])
                self.assertEqual(response.headers['Content-Range'], f'bytes {start}-{end}/{len(DATA)}')
                self.assertEqual(response.headers['Content-Length'], str(end-start+1))
        response = self.client.get(self.url, headers={'Range': 'bytes=0-1', 'If-Range': '"old-validator"'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, DATA)

    def test_invalid_and_multiple_ranges_416(self):
        for value in ('', 'bytes=', 'bytes=-', 'bytes=-0', 'bytes=9-2', f'bytes={len(DATA)}-',
                      'items=0-1', 'bytes=1.0-3', 'bytes=0-1,4-5', 'bytes=0 - 1',
                      'bytes=１-３', 'bytes=' + '9'*100 + '-'):
            with self.subTest(value=value):
                response = self.client.get(self.url, headers={'Range': value.encode('utf-8')})
                self.assertEqual(response.status_code, 416, response.text)
                self.assertEqual(response.json()['error']['code'], 'INVALID_CLIP_RANGE')
                self.assertEqual(response.headers['Content-Range'], f'bytes */{len(DATA)}')
                self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
        response = self.client.get(self.url, headers=[('Range', 'bytes=0-1'), ('Range', 'bytes=2-3')])
        self.assertEqual(response.status_code, 416)

    def test_no_path_camera_missing_event_and_invalid_id(self):
        person = self.create(appeared())
        response = self.client.get(MOBILE + f"/{person['event_id']}/clip")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'EVENT_CLIP_NOT_FOUND')
        for kind in ('camera_started', 'camera_stopped'):
            event = self.create(dict(event_type=kind, camera_id=1,
                camera_execution_id=str(uuid4()), occurred_at=WHEN))
            self.assertEqual(self.client.get(MOBILE + f"/{event['event_id']}/clip").status_code, 404)
        response = self.client.get(MOBILE + '/9999/clip', headers={'Range': 'invalid'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'EVENT_NOT_FOUND')
        for event_id in ('0', '-1', 'abc', '1.5', '9223372036854775808'):
            self.assertEqual(self.client.get(MOBILE + f'/{event_id}/clip').status_code, 422)

    def test_missing_empty_directory_storage_and_open_failure(self):
        self.path.unlink()
        self.assert_missing()
        self.path.touch()
        self.assert_missing()
        self.path.unlink()
        self.path.mkdir()
        self.assert_missing()
        self.path.rmdir()
        self.path.write_bytes(DATA)
        for root in (None, self.base / 'missing' / 'AI_CCTV'):
            with patch.object(self.app.state.storage_service, 'root', root):
                self.assert_missing()
                self.assertIsNotNone(self.client.get(MOBILE + f"/{self.event['event_id']}").json()['clip_url'])
        with patch.object(Path, 'open', side_effect=PermissionError('private internal path')):
            self.assert_missing()

    def test_tampered_path_and_junction_rejected(self):
        for value in ('../outside.mp4', str(self.path), 'C:/outside.mp4',
                      media('관측', 'crop.jpg'), media('관측', 'clip.mp4').replace('person_1', 'person_2')):
            with closing(sqlite3.connect(self.base / 'test.db')) as db:
                db.execute('UPDATE events SET clip_path=? WHERE event_id=?', (value, self.event['event_id']))
                db.commit()
            self.assert_missing()
        with closing(sqlite3.connect(self.base / 'test.db')) as db:
            db.execute('UPDATE events SET clip_path=? WHERE event_id=?',
                       (media('관측', 'clip.mp4'), self.event['event_id']))
            db.commit()
        folder = self.path.parent
        target = folder.with_name('real-observation')
        folder.rename(target)
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(folder), str(target)], capture_output=True, check=True)
        else:
            folder.symlink_to(target, target_is_directory=True)
        try:
            self.assert_missing()
        finally:
            if os.name == 'nt':
                os.rmdir(folder)  # 임시 junction 자체만 제거한다.
            else:
                folder.unlink()

    def test_same_url_after_registered_folder_change(self):
        self.path.parent.rename(self.path.parent.with_name('관측~마지막'))
        self.assert_missing()
        self.assertEqual(self.client.patch(EVENTS + f"/{self.event['event_id']}/media",
                         json={'clip_path': media('관측~마지막', 'clip.mp4')}).status_code, 200)
        self.assertEqual(self.client.get(self.url).content, DATA)

    def test_auth_missing_refresh_expired_logout(self):
        for token in (None, self.tokens['refresh_token']):
            self.client.headers.pop('Authorization', None)
            if token:
                self.client.headers['Authorization'] = 'Bearer ' + token
            response = self.client.get(self.url, headers={'Range': 'bytes=0-1'})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('Content-Range', response.headers)
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        auth = self.app.state.auth_service
        with patch.object(auth, 'clock', return_value=auth.clock()+901):
            self.assertEqual(self.client.get(self.url).status_code, 401)
        self.client.post('/api/v1/auth/logout', json={'refresh_token': self.tokens['refresh_token']})
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_file_closed_after_responses_and_asgi_disconnect(self):
        opened = []
        original = self.app.state.mobile_event_service.open_clip
        def record(event_id):
            result = original(event_id)
            opened.append(result[0])
            return result
        with patch.object(self.app.state.mobile_event_service, 'open_clip', side_effect=record):
            for headers in ({}, {'Range': 'bytes=0-1'}, {'Range': 'invalid'}):
                self.client.get(self.url, headers=headers)
                self.assertTrue(opened[-1].closed)
        handle = BytesIO(DATA)
        response = _ClipResponse(handle, len(DATA), media_type='video/mp4')
        async def disconnected():
            async def send(message):
                raise OSError('Disconnected client')
            async def receive():
                return {'type': 'http.disconnect'}
            with self.assertRaises(Exception):
                await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
        asyncio.run(disconnected())
        self.assertTrue(handle.closed)


class MobileClipHTTPTests(unittest.TestCase):
    def test_real_http_mp4_full_range_and_decode(self):
        import cv2
        import numpy as np
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'AI_CCTV'
            root.mkdir()
            write_media(root)
            path = root / media('관측', 'clip.mp4')
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
            self.assertTrue(writer.isOpened())
            try:
                for index in range(10):
                    writer.write(np.full((64, 64, 3), index * 20, dtype=np.uint8))
            finally:
                writer.release()
            data = path.read_bytes()
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(create_app(Path(tmp) / 'http.db'), log_level='critical'))
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic()+10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(server.started)
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5) as client:
                    self.assertEqual(client.get(MOBILE + '/1/clip').status_code, 401)
                    seed(client)
                    self.assertEqual(client.put('/api/v1/settings/storage',
                                     json={'storage_root': str(root)}).status_code, 200)
                    body = appeared(clip_path=media('관측', 'clip.mp4'))
                    person = client.post(EVENTS, json=body).json()
                    gone_response = client.post(EVENTS, json=body | {'event_type': 'person_disappeared',
                        'clip_path': None, 'related_event_id': person['event_id'],
                        'occurred_at': '2026-11-19T03:00:05Z'})
                    self.assertEqual(gone_response.status_code, 201, gone_response.text)
                    for event in (person, gone_response.json()):
                        url = client.get(MOBILE + f"/{event['event_id']}").json()['clip_url']
                        response = client.get(url)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers['Content-Type'], 'video/mp4')
                        self.assertEqual(response.content, data)
                        partial = client.get(url, headers={'Range': 'bytes=0-31'})
                        self.assertEqual(partial.status_code, 206)
                        self.assertEqual(partial.content, data[:32])
                        self.assertEqual(partial.headers['Content-Range'], f'bytes 0-31/{len(data)}')
                    saved = Path(tmp) / 'received.mp4'
                    saved.write_bytes(response.content)
                    capture = cv2.VideoCapture(str(saved))
                    try:
                        count = 0
                        while capture.read()[0]:
                            count += 1
                        self.assertEqual(count, 10)
                    finally:
                        capture.release()
                    self.assertEqual(client.get(url, headers={'Range': 'bytes=0-1,4-5'}).status_code, 416)
                    path.unlink()
                    self.assertEqual(client.get(url).status_code, 404)
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                sock.close()
            self.assertFalse(thread.is_alive())
