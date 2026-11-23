"""카메라 이벤트/큐/종료를 합성 입력과 임시 HTTP로 검증한다. 실장치·운영 DB 미사용."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread, get_ident
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import httpx
import requests
import uvicorn
from PyQt5.QtCore import QObject, pyqtSignal
from test_client_auth import APP, wait_for
from test_camera_runtime import FakeCameraApi, FakeWorker
from cameras.runtime_manager import CameraRuntimeManager
from events.coordinator import EventCoordinator
from events.store import EventStore
from workers.video_worker import VideoWorker
from ui.settings_window import SettingsWindow
from client_code.backend.main import create_app


CAM = dict(camera_id=1, name='test', camera_type='webcam', webcam_index=0,
           rtsp_url=None, edge_api_base_url=None, is_enabled=True)


class MemoryCoordinator(QObject):
    changed = pyqtSignal()
    drained = pyqtSignal(str)
    root = 'C:/test/AI_CCTV'
    ready = True
    finished = False

    def __init__(self):
        super().__init__()
        self.events = []

    def camera_event(self, *args):
        self.events.append(args)
        return True

    def begin_shutdown(self):
        self.finished = True
        self.drained.emit('')


class LifecycleWorker(FakeWorker):
    first_frame_ready = pyqtSignal(object)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.execution_id = uuid4().hex
        self.first_frame_at = self.cleanup_finished_at = None
        self.execution_failed = False

    def first(self):
        if self.first_frame_at is None:
            self.first_frame_at = datetime.now(timezone.utc)
        self.first_frame_ready.emit(self.first_frame_at)

    def request_stop(self):
        super().request_stop()
        self.active = False
        self.cleanup_finished_at = datetime.now(timezone.utc)


class CameraEventTests(unittest.TestCase):
    def setUp(self):
        self.runtime = CameraRuntimeManager(api_factory=FakeCameraApi)
        self.runtime.bind_session(object())
        self.store = MemoryCoordinator()
        self.runtime.attach_event_store(self.store)

    def tearDown(self):
        self.runtime._stop_timer.stop()
        self.runtime.deleteLater()
        self.store.deleteLater()
        APP.processEvents()

    def launch(self):
        self.assertTrue(self.runtime.request_detail(CAM, 'start', {'use_yolo': False, 'ai_cctv_path': 'wrong'}))
        run = self.runtime.runs[1]
        with patch('workers.video_worker.VideoWorker', LifecycleWorker):
            run.api.completed.emit(CAM, '', '')
        self.assertEqual(run.worker.values['ai_cctv_path'], self.store.root)
        return run

    def test_first_frame_once_repeat_start_stop_restart_and_late_signals(self):
        first = self.launch()
        self.assertEqual(self.store.events, [])
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        first.worker.first()
        first.worker.first()
        self.runtime.stop(1)
        self.runtime.stop(1)
        self.runtime._poll_stop()
        self.assertEqual([e[0] for e in self.store.events], ['camera_started', 'camera_stopped'])
        self.assertEqual(self.store.events[0][3], first.worker.first_frame_at)
        self.assertEqual(self.store.events[1][3], first.worker.cleanup_finished_at)
        second = self.launch()
        first.worker.first_frame_ready.emit(first.worker.first_frame_at)
        first.worker.finished.emit()
        self.assertFalse(second.stopping)
        self.assertEqual(len(self.store.events), 2)
        second.worker.first()
        second.worker.status_ready.emit('연결 대기 / 재연결 중')
        second.worker.first()
        self.runtime.stop(1)
        self.runtime._poll_stop()
        self.assertEqual(len(self.store.events), 4)
        self.assertNotEqual(self.store.events[0][2], self.store.events[2][2])

    def test_stop_before_first_and_cancelled_detail_have_no_events(self):
        self.launch()
        self.runtime.stop(1)
        self.runtime._poll_stop()
        self.runtime.request_detail(CAM, 'start')
        self.runtime.stop(1)
        self.runtime._poll_stop()
        self.assertFalse(self.store.events)

    def test_error_finish_has_start_only_and_cleanup_failure_never_stops(self):
        first = self.launch()
        first.worker.first()
        first.worker.execution_failed = True
        first.worker.active = False
        first.worker.finished.emit()
        self.runtime._poll_stop()
        self.assertEqual([e[0] for e in self.store.events], ['camera_started'])
        second = self.launch()
        second.worker.first_frame_at = datetime.now(timezone.utc)  # 큐 신호가 아직 전달되지 않은 경우
        second.worker.cleanup_ok = False
        self.runtime.stop(1)
        self.runtime._poll_stop()
        self.assertEqual([e[0] for e in self.store.events], ['camera_started', 'camera_started'])

    def test_shutdown_keeps_late_first_frame_and_drains_after_stop(self):
        run = self.launch()
        run.worker.first_frame_at = datetime.now(timezone.utc)
        done = []
        self.runtime.shutdown_complete.connect(lambda: done.append(self.store.finished))
        self.runtime.begin_shutdown()
        self.runtime._poll_stop()
        self.assertEqual([e[0] for e in self.store.events], ['camera_started', 'camera_stopped'])
        self.assertEqual(done, [True])

    def test_unconfirmed_storage_blocks_start(self):
        self.store.ready = False
        self.assertFalse(self.runtime.can_start(CAM))
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))


class SyntheticStream:
    is_rtsp = False
    def __init__(self, worker, mode='normal'):
        self.worker, self.mode, self.count = worker, mode, 0
    def open(self): return self.mode != 'open_failed'
    def get_fps(self): return 24
    def pop_connection_events(self): return []
    def release(self): return True
    def read(self):
        self.count += 1
        if self.mode == 'first_failed' or (self.mode == 'later_failed' and self.count > 1):
            return False, None
        if self.mode == 'stop' and self.count > 2:
            self.worker.request_stop()
            return False, None
        time.sleep(.005)
        return True, np.zeros((48, 64, 3), dtype=np.uint8)


class WorkerLifecycleTests(unittest.TestCase):
    def test_actual_worker_first_timestamp_and_normal_cleanup(self):
        worker = VideoWorker(use_yolo=False)
        worker.stream = SyntheticStream(worker, 'stop')
        received = []
        worker.first_frame_ready.connect(received.append)
        worker.run()
        self.assertEqual(received, [worker.first_frame_at])
        self.assertEqual(worker.first_frame_at.tzinfo, timezone.utc)
        self.assertLessEqual(worker.first_frame_at, worker.cleanup_finished_at)
        self.assertTrue(worker.cleanup_ok)
        self.assertTrue(worker.stop_requested)
        self.assertFalse(worker.execution_failed)

    def test_open_first_frame_and_later_failures_are_not_normal_stop(self):
        for mode in ('open_failed', 'first_failed', 'later_failed'):
            worker = VideoWorker(use_yolo=False)
            worker.stream = SyntheticStream(worker, mode)
            worker.run()
            self.assertTrue(worker.execution_failed)
            self.assertTrue(worker.cleanup_ok)
            self.assertEqual(worker.first_frame_at is None, mode != 'later_failed')


class Session(QObject):
    refresh_completed = pyqtSignal(str)
    signed_out = pyqtSignal(str)
    expired = pyqtSignal(str)
    def __init__(self, base_url, token):
        super().__init__()
        self.api = SimpleNamespace(base_url=base_url)
        self.access_token = token


class QueueTests(unittest.TestCase):
    def test_bounded_queue_snapshot_order_and_thread_separation(self):
        store = EventStore('http://unused', 'test-token', capacity=2)
        body = {'value': 1}
        self.assertIsNotNone(store.submit('POST', '/first', body))
        body['value'] = 99
        store.submit('POST', '/second')
        self.assertIsNone(store.submit('POST', '/overflow'))
        calls = []
        def request(_, job):
            calls.append((job['path'], job['body'], get_ident()))
            return {}, ''
        with patch.object(store, '_request', side_effect=request):
            store.finish()
            store.start()
            store.join(3)
        self.assertFalse(store.is_alive())
        self.assertEqual([(p, b) for p, b, _ in calls], [('/first', {'value': 1}), ('/second', None)])
        self.assertTrue(all(tid != get_ident() for _, _, tid in calls))

    def test_response_loss_retries_same_body_and_abort_prevents_retry(self):
        store = EventStore('http://unused', 'test-token')
        response = MagicMock(status_code=201)
        response.__enter__.return_value = response
        response.iter_content.return_value = [b'{"event_id":1}']
        transport = MagicMock()
        transport.request.side_effect = [requests.ConnectionError(), response]
        job = {'method': 'POST', 'path': '/events', 'body': {'key': 'same'}}
        payload, error = store._request(transport, job)
        self.assertEqual((payload, error), ({'event_id': 1}, ''))
        self.assertEqual(transport.request.call_count, 2)
        self.assertEqual(transport.request.call_args_list[0], transport.request.call_args_list[1])
        def fail(*args, **kwargs):
            store.abort()
            raise requests.ConnectionError()
        transport.request.reset_mock(side_effect=True)
        transport.request.side_effect = fail
        self.assertEqual(store._request(transport, job)[1], 'SAVE_CANCELLED')
        self.assertEqual(transport.request.call_count, 1)

    def test_shutdown_timeout_reports_unknown_result_and_old_results_are_ignored(self):
        store = MagicMock()
        store.submit.return_value = 'pending'
        store.take_results.return_value = []
        store.is_alive.return_value = True
        session = Session('http://unused', 'test-token')
        coordinator = EventCoordinator(session, store_factory=lambda *_: store)
        warnings = []
        coordinator.drained.connect(warnings.append)
        coordinator.begin_shutdown(timeout=0)
        self.assertTrue(coordinator.finished)
        self.assertTrue(warnings[0])
        store.abort.assert_called()
        previous_calls = store.submit.call_count
        coordinator.poll()
        self.assertEqual(store.submit.call_count, previous_calls)
        coordinator.deleteLater()

    def test_refresh_updates_token_during_drain_and_signed_out_aborts(self):
        store = MagicMock()
        store.submit.return_value = 'get'
        store.take_results.return_value = []
        session = Session('http://unused', 'old-token')
        coordinator = EventCoordinator(session, store_factory=lambda *_: store)
        coordinator.closing = True
        session.access_token = 'new-token'
        session.refresh_completed.emit('')
        store.set_access_token.assert_called_once_with('new-token')
        session.signed_out.emit('')
        store.abort.assert_called_once()
        coordinator.timer.stop()
        coordinator.deleteLater()

    def test_wrong_camera_response_is_reported_as_failure(self):
        store = MagicMock()
        store.submit.side_effect = ['get', 'camera']
        store.take_results.return_value = []
        session = Session('http://unused', 'test-token')
        coordinator = EventCoordinator(session, store_factory=lambda *_: store)
        timestamp = datetime.now(timezone.utc)
        execution = uuid4().hex
        coordinator.camera_event('camera_started', 1, execution, timestamp)
        store.take_results.return_value = [dict(request_id='camera', error='', payload={
            'event_id': 1, 'server_run_id': 1, 'camera_id': 2, 'event_type': 'camera_started',
            'camera_execution_id': execution, 'occurred_at': timestamp.isoformat()})]
        coordinator.poll()
        self.assertEqual(coordinator.failures, 1)
        coordinator.timer.stop()
        coordinator.deleteLater()


@contextmanager
def backend():
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / 'AI_CCTV'
        root.mkdir()
        app = create_app(Path(tmp) / 'events.db')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        base_url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical'))
        thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            wait_for(lambda: server.started, seconds=10)
            with httpx.Client(base_url=base_url, timeout=5) as client:
                client.post('/api/v1/auth/signup', json={'username': 'admin', 'password': 'test-pass', 'password_confirm': 'test-pass'})
                tokens = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-pass'}).json()
                client.headers['Authorization'] = 'Bearer ' + tokens['access_token']
                client.post('/api/v1/cameras', json={'name': 'test', 'camera_type': 'webcam', 'webcam_index': 0})
                yield root, Session(base_url, tokens['access_token']), client
        finally:
            server.should_exit = True
            thread.join(10)
            sock.close()
            if thread.is_alive():
                raise AssertionError('Temporary backend did not stop')


class LiveCameraEventTests(unittest.TestCase):
    def test_actual_worker_queue_http_restart_and_shutdown(self):
        with backend() as (root, session, client):
            store = EventCoordinator(session)
            runtime = CameraRuntimeManager(api_factory=FakeCameraApi)
            runtime.bind_session(session)
            runtime.attach_event_store(store)
            try:
                wait_for(lambda: store.loaded)
                self.assertFalse(runtime.can_start(CAM))
                self.assertTrue(store.set_root(str(root)))
                wait_for(lambda: store.ready)
                self.assertEqual(store.root, str(root))
                def factory(**kwargs):
                    worker = VideoWorker(**kwargs)
                    worker.stream = SyntheticStream(worker)
                    return worker
                for index in range(2):
                    self.assertTrue(runtime.request_detail(CAM, 'start', {'use_yolo': False}))
                    run = runtime.runs[1]
                    with patch('workers.video_worker.VideoWorker', side_effect=factory), patch('workers.video_worker.RecordingManager'):
                        run.api.completed.emit(CAM, '', '')
                        wait_for(lambda: run.start_event_attempted)
                        runtime.stop(1)
                        wait_for(lambda: 1 not in runtime.runs)
                    wait_for(lambda: not store._pending)
                    rows = client.get('/api/v1/events').json()['items']
                    self.assertEqual(len(rows), (index + 1) * 2)
                    self.assertEqual({r['event_type'] for r in rows}, {'camera_started', 'camera_stopped'})
                self.assertEqual(len({r['camera_execution_id'] for r in rows}), 2)
                done = []
                runtime.shutdown_complete.connect(lambda: done.append(True))
                runtime.begin_shutdown()
                wait_for(lambda: bool(done))
                self.assertTrue(store.finished)
                self.assertFalse(store.failures)
                self.assertEqual(client.get('/api/v1/events').status_code, 200)  # 큐 완료까지 인증 유지
            finally:
                for run in list(runtime.runs.values()):
                    if run.worker:
                        run.worker.request_stop()
                        run.worker.wait(5000)
                store.store.abort()
                store.store.join(5)
                store.timer.stop()
                runtime._stop_timer.stop()
                runtime.deleteLater()
                store.deleteLater()
                APP.processEvents()

    def test_settings_save_waits_for_server_and_locked_path_is_disabled(self):
        with backend() as (root, session, client):
            store = EventCoordinator(session)
            try:
                wait_for(lambda: store.loaded)
                dialog = SettingsWindow(event_store=store)
                dialog.ai_cctv_path = str(root)
                dialog.save_storage_settings()
                self.assertEqual(dialog.result(), 0)
                wait_for(lambda: dialog.result() == dialog.Accepted)
                self.assertEqual(dialog.ai_cctv_path, store.root)
                dialog.deleteLater()
                client.post('/api/v1/events', json={'event_type': 'camera_started', 'camera_id': 1,
                    'camera_execution_id': str(uuid4()), 'occurred_at': datetime.now(timezone.utc).isoformat()})
                store.refresh_storage()
                wait_for(lambda: store.loaded and not store.settings_pending)
                dialog = SettingsWindow(event_store=store, ai_cctv_path=store.root)
                self.assertFalse(dialog.btn_select_storage_path.isEnabled())
                dialog.deleteLater()
            finally:
                store.begin_shutdown()
                wait_for(lambda: store.finished)
                store.store.join(5)
                store.deleteLater()
                APP.processEvents()


if __name__ == '__main__':
    unittest.main()
