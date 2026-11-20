"""임시 FastAPI/DB와 Qt 네트워크를 연결한 타임라인 이력 검증."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from uuid import uuid4

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from fastapi.responses import JSONResponse
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from auth.api_client import AuthApiClient
from auth.session_manager import SessionManager
from events.history import EventHistory, validate_page
from ui.event_timeline import EventCard, EventTimeline
from client_code.backend.main import create_app

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        APP.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError('Qt 작업 시간 초과')
        time.sleep(.005)
    APP.processEvents()


class EventHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        base = Path(cls.tmp.name)
        root = base / 'AI_CCTV'
        root.mkdir()
        app = create_app(base / 'test.db', base / 'storage.json')
        cls.mode = ''
        cls.snapshot_ready = False
        cls.requests = []

        @app.middleware('http')
        async def probe(request, call_next):
            if request.url.path == '/api/v1/events' and request.method == 'GET':
                cls.requests.append(dict(request.query_params))
                if cls.mode == 'slow':
                    await asyncio.sleep(.2)
                if cls.mode == 'snapshot':
                    response = await call_next(request)
                    cls.snapshot_ready = True
                    await asyncio.sleep(.2)
                    return response
                if cls.mode == 'invalid':
                    return JSONResponse({'items': [{'event_id': True}]})
                if cls.mode == 'empty':
                    return JSONResponse({'items': []})
                if cls.mode == 'error':
                    return JSONResponse({}, status_code=500)
                if cls.mode in ('unauthorized_once', 'unauthorized_always'):
                    if cls.mode == 'unauthorized_once':
                        cls.mode = ''
                    return JSONResponse({}, status_code=401)
            return await call_next(request)

        cls.sock = socket.socket()
        cls.sock.bind(('127.0.0.1', 0))
        cls.url = f'http://127.0.0.1:{cls.sock.getsockname()[1]}'
        cls.server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        cls.thread = Thread(target=lambda: cls.server.run(sockets=[cls.sock]), daemon=True)
        cls.thread.start()
        wait_for(lambda: cls.server.started)
        with httpx.Client(base_url=cls.url) as client:
            account = dict(username='admin', password='test-pass')
            client.post('/api/v1/auth/signup', json=account | {'password_confirm': 'test-pass'}).raise_for_status()
            token = client.post('/api/v1/auth/login', json=account).json()['access_token']
            client.headers['Authorization'] = 'Bearer ' + token
            client.put('/api/v1/settings/storage', json={'storage_root': str(root)}).raise_for_status()
            client.post('/api/v1/cameras', json=dict(name='test', camera_type='webcam', webcam_index=0)).raise_for_status()
            stamp = datetime(2026, 11, 19, 14, 59, 40, tzinfo=timezone.utc)
            cls.ids = []
            for i in range(55):
                body = dict(event_type='camera_started', camera_id=1, camera_execution_id=str(uuid4()),
                            occurred_at=(stamp + timedelta(seconds=i)).isoformat())
                result = client.post('/api/v1/events', json=body)
                result.raise_for_status()
                cls.ids.append(result.json()['event_id'])

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(5)
        cls.sock.close()
        cls.tmp.cleanup()

    def setUp(self):
        type(self).mode = ''
        type(self).snapshot_ready = False
        self.requests.clear()
        self.session = SessionManager(AuthApiClient(self.url))
        self.session.login('admin', 'test-pass')
        wait_for(lambda: self.session.access_token is not None)
        self.panel = EventTimeline()
        self.panel.resize(300, 600)
        self.panel.show()
        self.history = EventHistory(self.session, self.panel)

    def tearDown(self):
        self.history.stop()
        self.history.deleteLater()
        self.panel.close()
        self.panel.deleteLater()
        self.session.refresh_timer.stop()
        self.session.deadline_timer.stop()
        self.session.deleteLater()
        APP.processEvents()

    def load(self):
        self.history.load_more()
        wait_for(lambda: not self.history.pending)

    def test_first_50_then_older_db_cards_cursor_and_end(self):
        self.load()
        self.assertEqual(set(self.history.events), set(self.ids[-50:]))
        self.assertTrue(self.panel.more_button.isEnabled())
        self.panel.more_button.click()
        wait_for(lambda: not self.history.pending)
        self.assertEqual(set(self.history.events), set(self.ids))
        self.assertEqual(self.requests[0], {'limit': '50'})
        self.assertEqual(int(self.requests[1]['before_event_id']), self.ids[5])
        self.assertIn('before_occurred_at', self.requests[1])
        self.assertFalse(self.history.has_more)
        self.assertTrue(self.panel.more_button.isHidden())
        cards = [self.panel.items.itemAt(i).widget() for i in range(self.panel.items.count())
                 if isinstance(self.panel.items.itemAt(i).widget(), EventCard)]
        self.assertEqual(len(cards), 55)
        self.assertEqual(cards[0].time_label.text(), '00:00:34')
        self.history.load_more()
        self.assertEqual(len(self.requests), 2)

    def test_failure_keeps_cards_cursor_retry_and_invalid_page(self):
        self.load()
        old_cursor = self.history.cursor
        type(self).mode = 'error'
        self.load()
        self.assertEqual(len(self.history.events), 50)
        self.assertEqual(self.history.cursor, old_cursor)
        self.assertEqual(self.panel.more_button.text(), '다시 시도')
        type(self).mode = 'invalid'
        self.load()
        self.assertEqual(len(self.history.events), 50)
        self.assertEqual(self.history.cursor, old_cursor)
        type(self).mode = ''
        self.panel.more_button.click()
        wait_for(lambda: not self.history.pending)
        self.assertEqual(len(self.history.events), 55)

    def test_auth_refresh_once_and_repeated_401_expires(self):
        token = self.session.access_token
        type(self).mode = 'unauthorized_once'
        self.load()
        self.assertNotEqual(self.session.access_token, token)
        self.assertEqual(len(self.history.events), 50)
        type(self).mode = 'unauthorized_always'
        self.load()
        self.assertTrue(self.session.expiry_reported)
        self.assertTrue(self.history.stopped)
        self.assertEqual(len(self.history.events), 50)

    def test_stop_ignores_late_response_and_query_does_not_block_qt(self):
        type(self).mode = 'slow'
        heartbeat = []
        QTimer.singleShot(20, lambda: heartbeat.append(True))
        self.history.load_more()
        self.history.load_more()
        wait_for(lambda: bool(heartbeat))
        self.assertTrue(self.history.pending)
        self.session.signed_out.emit('')
        wait_for(lambda: self.history.stopped)
        deadline = time.monotonic() + .3
        wait_for(lambda: time.monotonic() >= deadline)
        self.assertEqual(self.history.events, {})
        self.assertEqual(len(self.requests), 1)

    def test_timeout_and_initial_error_are_retryable_not_empty_success(self):
        type(self).mode = 'slow'
        self.history.timeout_ms = 15
        self.load()
        self.assertEqual(self.history.events, {})
        self.assertFalse(self.panel.query_status.isHidden())
        self.assertTrue(self.panel.empty_label.isHidden())
        self.assertTrue(self.panel.more_button.isEnabled())
        type(self).mode = ''
        self.history.timeout_ms = 10000
        self.load()
        self.assertEqual(len(self.history.events), 50)

    def test_page_rejects_bad_identity_timezone_order_and_accepts_empty(self):
        event = dict(event_id=1, camera_id=1, server_run_id=1, event_type='person_appeared',
                     global_person_id=7, occurred_at='2026-11-20T00:00:00Z')
        self.assertEqual(validate_page({'items': []}, None), [])
        for change in ({'event_id': True}, {'global_person_id': None},
                       {'occurred_at': '2026-11-20T00:00:00'}, {'event_type': 'unknown'}):
            with self.assertRaises(ValueError):
                validate_page({'items': [event | change]}, None)
        with self.assertRaises(ValueError):
            validate_page({'items': [event, event]}, None)

    def test_empty_history_is_success_and_session_expiry_cancels(self):
        type(self).mode = 'empty'
        self.load()
        self.assertFalse(self.history.has_more)
        self.assertTrue(self.panel.query_status.isHidden())
        self.assertFalse(self.panel.empty_label.isHidden())
        self.session.expired.emit('expired')
        self.assertTrue(self.history.stopped)
        self.history.load_more()
        self.assertEqual(len(self.requests), 1)

    def test_main_window_binds_history_and_shutdown_stops_only_reads(self):
        from ui.gui import CCTVMainWindow
        window = CCTVMainWindow()
        try:
            window.bind_camera_session(self.session)
            wait_for(lambda: not window.event_history.pending)
            self.assertEqual(len(window.event_history.events), 50)
            self.assertEqual(window.video_grid.count(), 4)
            self.assertIsNot(window.event_history.network, window.camera_api.network)
            window.begin_shutdown()
            self.assertTrue(window.event_history.stopped)
            wait_for(lambda: window.runtime.event_store.finished)
            self.assertTrue(self.session.access_token)
        finally:
            if window.event_history is not None:
                window.event_history.stop()
            if window.runtime.event_store is not None:
                window.runtime.event_store.store.abort()
                window.runtime.event_store.store.join(4)
            window.allow_close = True
            window.close()
            window.deleteLater()


if __name__ == '__main__':
    unittest.main()
