"""GUI 이벤트 루프·인증 HTTP·소유 서버 종료 검증. 실제 DB/카메라는 사용하지 않는다."""

from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QObject, QProcess, QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLineEdit

from app_controller import AppController
from auth.api_client import AuthApiClient
from auth.backend_process import BackendProcess
from auth.session_manager import SessionManager
from ui.auth_window import AuthWindow
from ui.gui import CCTVMainWindow
from cameras.runtime_manager import CameraRun
from ui.resource_monitor_window import ResourceMonitorWindow
from workers.vlm_worker import VLMWorker
from workers.video_worker import VideoWorker


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    if not predicate():
        raise AssertionError("Expected condition did not complete before timeout")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def tokens(refresh="refresh-one", access="access-one", remaining=28800):
    return {"user": {"user_id": 1, "username": "admin"}, "token_type": "bearer",
            "access_token": access, "refresh_token": refresh,
            "access_expires_in": 900, "refresh_expires_in": remaining}


class FakeApi(QObject):
    completed = pyqtSignal(str, object, str)

    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, action, body=None):
        self.calls.append((action, body))
        return True

    def finish(self, action, payload=None, error=""):
        self.completed.emit(action, payload, error)


class FakeBackend(QObject):
    ready = pyqtSignal()
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.is_ready = False
        self.stop_count = 0

    def start(self):
        self.is_ready = True
        self.ready.emit()

    def stop(self):
        self.is_ready = False
        self.stop_count += 1
        self.stopped.emit()


class FakeVideo(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = True
        self.cleanup_ok = True
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True

    def isRunning(self):
        return self.active


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.session = SessionManager(self.api)
        self.addCleanup(self.session.refresh_timer.stop)
        self.addCleanup(self.session.deadline_timer.stop)

    def login(self):
        self.session.login("admin", "test-pass")
        self.api.finish("login", tokens())

    def test_duplicate_requests_and_refresh_rotation(self):
        self.login()
        deadline = self.session.deadline
        self.session.refresh()
        self.session.refresh()
        self.assertEqual([item[0] for item in self.api.calls], ["login", "refresh"])
        self.api.finish("refresh", tokens("new-refresh", "new-access", 28800))
        self.assertLessEqual(self.session.deadline, deadline)
        self.assertEqual(self.session.refresh_token, "new-refresh")
        self.assertTrue(self.session.refresh_timer.isActive())

    def test_logout_waits_for_refresh_and_clears_tokens(self):
        self.login()
        self.session.refresh()
        self.session.logout()
        self.assertEqual(self.api.calls[-1][0], "refresh")
        self.api.finish("refresh", tokens("latest", "new-access"))
        self.assertEqual(self.api.calls[-1], ("logout", {"refresh_token": "latest"}))
        self.api.finish("logout", {})
        self.assertIsNone(self.session.refresh_token)
        self.assertIsNone(self.session.access_token)
        self.assertFalse(self.session.refresh_timer.isActive())

    def test_close_during_login_revokes_new_session(self):
        success = []
        self.session.authenticated.connect(lambda: success.append(True))
        self.session.login("admin", "test-pass")
        self.session.logout()
        self.api.finish("login", tokens())
        self.assertFalse(success)
        self.assertEqual(self.api.calls[-1][0], "logout")
        self.api.finish("logout", {})
        self.assertIsNone(self.session.user)

    def test_logout_network_failure_still_clears_local_tokens(self):
        warnings = []
        self.session.signed_out.connect(warnings.append)
        self.login()
        self.session.logout()
        self.api.finish("logout", error="CONNECTION_ERROR")
        self.assertIsNone(self.session.refresh_token)
        self.assertTrue(warnings[0])

    def test_refresh_connection_error_retries_but_401_expires(self):
        expired = []
        self.session.expired.connect(expired.append)
        self.login()
        self.session.refresh()
        self.api.finish("refresh", error="CONNECTION_ERROR")
        self.assertFalse(expired)
        self.assertTrue(self.session.refresh_timer.isActive())
        self.session.refresh()
        self.api.finish("refresh", error="INVALID_REFRESH_TOKEN")
        self.assertEqual(len(expired), 1)
        self.assertFalse(self.session.refresh_timer.isActive())

    def test_absolute_deadline_cannot_be_extended(self):
        expired = []
        self.session.expired.connect(expired.append)
        self.login()
        self.session.deadline = time.monotonic() - 1
        self.session._check_deadline()
        self.session._check_deadline()
        self.assertEqual(len(expired), 1)
        self.session.refresh()
        self.assertEqual(len(self.api.calls), 1)

    def test_invalid_success_payload_is_not_login_success(self):
        errors = []
        self.session.error.connect(lambda *values: errors.append(values))
        self.session.login("admin", "test-pass")
        self.api.finish("login", {"access_token": "unusable"})
        self.assertIsNone(self.session.access_token)
        self.assertEqual(errors[0][1], "INVALID_RESPONSE")


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.session = SessionManager(self.api)
        self.backend = FakeBackend()
        self.controller = AppController(self.session, self.backend, CCTVMainWindow)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.session.refresh_timer.stop()
        self.session.deadline_timer.stop()
        main = self.controller.main_window
        if main:
            main.runtime._stop_timer.stop()
            main.allow_close = True
            main.close()
            main.deleteLater()
        self.controller.auth_window.allow_close = True
        self.controller.auth_window.close()
        self.controller.auth_window.deleteLater()

    def authenticate(self):
        self.controller.start()
        self.api.finish("signup-status", {"signup_required": False})
        self.session.login("admin", "test-pass")
        # 이 테스트는 가짜 인증 API만 사용하며 카메라 HTTP 연결은 별도 통합 테스트에서 검증한다.
        with patch.object(CCTVMainWindow, "bind_camera_session"):
            self.api.finish("login", tokens())

    def test_signup_login_visibility_and_validation(self):
        window = self.controller.auth_window
        self.controller.start()
        self.assertIsNone(self.controller.main_window)
        self.api.finish("signup-status", {"signup_required": True})
        self.assertEqual(window.mode, "signup")
        self.assertTrue(window.confirm.isVisible())
        self.assertEqual(window.password.echoMode(), QLineEdit.Password)
        window.username.setText("abc")
        window.password.setText("abcd")
        QTest.mouseClick(window.submit_button, Qt.LeftButton)
        self.assertEqual(len(self.api.calls), 1)
        window.username.setText("admin")
        window.confirm.setText("abcd")
        QTest.mouseClick(window.submit_button, Qt.LeftButton)
        self.assertFalse(window.submit_button.isEnabled())
        self.assertEqual(window.password.text(), "")
        self.api.finish("signup", {"user_id": 1, "username": "admin"})
        self.assertEqual(window.mode, "login")
        self.assertFalse(window.confirm.isVisible())

    def test_connection_failure_and_closed_signup(self):
        self.controller.start()
        self.api.finish("signup-status", error="CONNECTION_ERROR")
        self.assertEqual(self.controller.auth_window.mode, "error")
        self.assertIsNone(self.controller.main_window)
        QTest.mouseClick(self.controller.auth_window.retry_button, Qt.LeftButton)
        self.api.finish("signup-status", {"signup_required": True})
        self.session.signup("admin", "abcd", "abcd")
        self.api.finish("signup", error="SIGNUP_CLOSED")
        self.assertEqual(self.controller.auth_window.mode, "login")

    def test_logout_waits_for_video_and_keeps_server(self):
        self.authenticate()
        main = self.controller.main_window
        self.assertFalse(main.runtime.runs)
        video = FakeVideo(main)
        main.runtime.runs[1] = CameraRun(dict(camera_id=1, name='test', is_enabled=True), {}, 1, 0, worker=video, pending=False)
        other = FakeVideo(main)
        main.runtime.runs[2] = CameraRun(dict(camera_id=2, name='second', is_enabled=True), {}, 2, 1, worker=other, pending=False)
        QTest.mouseClick(main.btn_logout, Qt.LeftButton)
        QTest.qWait(150)
        self.assertTrue(video.stop_requested)
        self.assertNotEqual(self.api.calls[-1][0], "logout")
        self.assertFalse(main.btn_start.isEnabled())
        self.assertTrue(other.stop_requested)
        video.active = False
        main.runtime._poll_stop()
        self.assertNotEqual(self.api.calls[-1][0], 'logout')
        other.active = False
        wait_for(lambda: self.api.calls[-1][0] == "logout")
        self.api.finish("logout", {})
        self.assertEqual(self.controller.auth_window.mode, "login")
        self.assertIsNone(self.controller.main_window)
        self.assertEqual(self.backend.stop_count, 0)

    def test_cleanup_failure_blocks_transition(self):
        self.authenticate()
        main = self.controller.main_window
        video = FakeVideo(main)
        video.active = False
        video.cleanup_ok = False
        main.runtime.runs[1] = CameraRun(dict(camera_id=1, name='test', is_enabled=True), {}, 1, 0, worker=video, pending=False)
        main.btn_logout.click()
        QTest.qWait(150)
        self.assertIs(self.controller.main_window, main)
        self.assertNotEqual(self.api.calls[-1][0], "logout")
        self.assertIn("실패", main.statusBar().currentMessage())

    def test_session_expiry_waits_for_runtime_cleanup(self):
        self.authenticate()
        main = self.controller.main_window
        video = FakeVideo(main)
        main.runtime.runs[1] = CameraRun(dict(camera_id=1, name='test', is_enabled=True), {}, 1, 0, worker=video, pending=False)
        self.session.expired.emit('session expired')
        self.assertTrue(video.stop_requested)
        self.assertTrue(main.runtime.shutting_down)
        self.assertNotEqual(self.api.calls[-1][0], 'logout')
        video.active = False
        wait_for(lambda: self.api.calls[-1][0] == 'logout')
        self.api.finish('logout', {})
        self.assertIsNone(self.controller.main_window)
        self.assertIn('session expired', self.controller.auth_window.message.text())

    def test_logout_during_detail_cancels_before_session_revocation(self):
        self.authenticate()
        main = self.controller.main_window
        from test_camera_runtime import FakeCameraApi, CAM
        main.runtime.api_factory = FakeCameraApi
        main.runtime.bind_session(self.session)
        main.runtime.request_detail(CAM, 'start')
        run = main.runtime.runs[7]
        self.controller.request_logout()
        run.api.cancel.assert_called_once()
        self.assertFalse(run.pending)
        self.assertNotEqual(self.api.calls[-1][0], 'logout')
        wait_for(lambda: self.api.calls[-1][0] == 'logout')
        self.api.finish('logout', {})
        self.assertIsNone(self.controller.main_window)

    def test_close_waits_for_login_then_stops_owned_backend(self):
        self.controller.start()
        self.api.finish("signup-status", {"signup_required": False})
        self.session.login("admin", "test-pass")
        self.controller.auth_window.close()
        self.api.finish("login", tokens())
        self.assertIsNone(self.controller.main_window)
        self.api.finish("logout", {})
        self.assertEqual(self.backend.stop_count, 1)


class WorkerTests(unittest.TestCase):
    def test_resource_monitor_close_and_escape_wait_for_request(self):
        class PendingRequest(QThread):
            def __init__(self, gate, parent):
                super().__init__(parent)
                self.gate = gate

            def run(self):
                self.gate.wait(5)

        for escape in (False, True):
            with patch.object(ResourceMonitorWindow, "refresh"):
                window = ResourceMonitorWindow()
            gate = threading.Event()
            thread = PendingRequest(gate, window)
            window.remote_worker = thread
            thread.finished.connect(window.handle_smart_resource_finished)
            finished = []
            window.finished.connect(finished.append)
            window.show()
            thread.start()
            try:
                wait_for(thread.isRunning)
                if escape:
                    QTest.keyClick(window, Qt.Key_Escape)
                else:
                    window.close()
                self.assertFalse(finished)
                self.assertFalse(window.timer.isActive())
                gate.set()
                wait_for(lambda: bool(finished))
                self.assertIsNone(window.remote_worker)
                self.assertFalse(window.isVisible())
            finally:
                gate.set()
                if window.remote_worker is not None:
                    window.remote_worker.wait(5000)
                window.close()
                window.deleteLater()

    def test_stop_before_video_start_does_not_open_camera(self):
        with patch("workers.video_worker.CropManager"):
            worker = VideoWorker(use_yolo=False, use_vlm=False)
        worker.stream = MagicMock()
        worker.stream.release.return_value = True
        worker.request_stop()
        worker.run()
        worker.stream.open.assert_not_called()
        self.assertTrue(worker.cleanup_ok)

    def test_video_exception_still_finalizes_recordings_and_stream(self):
        with patch("workers.video_worker.CropManager"):
            worker = VideoWorker(use_yolo=False, use_vlm=False)
        worker.recording_manager = MagicMock()
        worker.clip_manager = MagicMock()
        worker.stream = MagicMock()
        worker.stream.release.return_value = True
        with patch.object(worker, "_run_video", side_effect=RuntimeError("test")):
            worker.run()
        worker.recording_manager.stop_recording.assert_called_once()
        worker.clip_manager.finish_all.assert_called_once()
        worker.stream.release.assert_called_once()
        self.assertTrue(worker.cleanup_ok)

    def test_failed_recording_cleanup_does_not_skip_other_resources(self):
        with patch("workers.video_worker.CropManager"):
            worker = VideoWorker(use_yolo=False, use_vlm=False)
        worker.recording_manager = MagicMock()
        worker.recording_manager.stop_recording.side_effect = OSError("test")
        worker.clip_manager = MagicMock()
        worker.stream = MagicMock()
        worker.stream.release.return_value = True
        worker._cleanup()
        self.assertFalse(worker.cleanup_ok)
        worker.clip_manager.finish_all.assert_called_once()
        worker.stream.release.assert_called_once()

    def test_cleanup_checks_rtsp_vlm_and_recovery_completion(self):
        with patch("workers.video_worker.CropManager"):
            worker = VideoWorker(use_yolo=False, use_vlm=False)
        worker.stream = MagicMock()
        worker.stream.release.side_effect = [False, True]
        worker.vlm_worker = MagicMock()
        worker.vlm_worker.stop.side_effect = [False, True]
        recovery = MagicMock()
        recovery.is_alive.side_effect = [True, False]
        worker.recovery_threads = [recovery]
        worker._cleanup()
        self.assertTrue(worker.cleanup_ok)
        self.assertEqual(worker.stream.release.call_count, 2)
        self.assertEqual(worker.vlm_worker.stop.call_count, 2)
        recovery.join.assert_called_once()

    def test_vlm_stop_does_not_cleanup_an_active_analysis(self):
        worker = VLMWorker(None)
        gate = threading.Event()
        worker.thread = threading.Thread(target=gate.wait)
        worker.thread.start()
        try:
            with patch.object(worker, "cleanup") as cleanup:
                self.assertFalse(worker.stop(timeout=0.01))
                cleanup.assert_not_called()
        finally:
            gate.set()
            worker.thread.join()
        self.assertTrue(worker.stop())

    def test_vlm_cleanup_runs_even_on_initialization_failure(self):
        worker = VLMWorker(None)
        with patch.object(worker, "_analyze_loop", side_effect=RuntimeError("test")):
            with patch.object(worker, "cleanup") as cleanup:
                with self.assertRaises(RuntimeError):
                    worker._run()
                cleanup.assert_called_once()


class HttpAndProcessTests(unittest.TestCase):
    def stop_backend(self, backend):
        backend.stop()
        wait_for(lambda: backend.process.state() == QProcess.NotRunning, 15)

    def test_real_http_signup_login_refresh_logout_and_process_restart(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "auth.db"
            port = free_port()
            backend = BackendProcess(db_path=path, port=port)
            session = SessionManager(AuthApiClient(f"http://127.0.0.1:{port}"))
            controller = AppController(session, backend, CCTVMainWindow)
            try:
                controller.start()
                wait_for(lambda: controller.auth_window.mode == "signup", 15)
                window = controller.auth_window
                window.username.setText("admin")
                window.password.setText("test-pass")
                window.confirm.setText("test-pass")
                QTest.mouseClick(window.submit_button, Qt.LeftButton)
                wait_for(lambda: window.mode == "login")
                window.password.setText("wrong-password")
                QTest.mouseClick(window.submit_button, Qt.LeftButton)
                wait_for(lambda: not session.busy)
                self.assertIsNone(controller.main_window)
                self.assertIn("올바르지", window.message.text())
                window.password.setText("test-pass")
                QTest.mouseClick(window.submit_button, Qt.LeftButton)
                wait_for(lambda: controller.main_window is not None)
                first = session.refresh_token
                session.refresh()
                wait_for(lambda: not session.busy)
                self.assertNotEqual(session.refresh_token, first)
                controller.main_window.btn_logout.click()
                wait_for(lambda: controller.main_window is None)
                self.assertEqual(window.mode, "login")
                self.assertTrue(backend.is_ready)
                self.assertIsNone(session.refresh_token)
                controller.request_exit()
                wait_for(lambda: backend.process.state() == QProcess.NotRunning)
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute("SELECT status FROM server_runs").fetchone()[0], "stopped")
                backend.start()
                wait_for(lambda: backend.is_ready)
                # Backend.ready는 동일 컨트롤러의 가입 상태 조회에 연결돼 있다.
                wait_for(lambda: not session.busy)
            finally:
                session.refresh_timer.stop()
                session.deadline_timer.stop()
                self.stop_backend(backend)
                window = controller.main_window
                if window:
                    window.allow_close = True
                    window.close()
                controller.auth_window.allow_close = True
                controller.auth_window.close()

    def test_other_server_is_not_stopped_on_port_conflict(self):
        with TemporaryDirectory() as temp:
            port = free_port()
            first = BackendProcess(db_path=Path(temp) / "one.db", port=port)
            second = BackendProcess(db_path=Path(temp) / "two.db", port=port)
            errors = []
            second.failed.connect(errors.append)
            try:
                first.start()
                wait_for(lambda: first.is_ready, 15)
                second.start()
                wait_for(lambda: bool(errors), 15)
                self.assertFalse(second.is_ready)
                self.assertTrue(first.is_ready)
                self.stop_backend(second)
                self.assertEqual(first.process.state(), QProcess.Running)
            finally:
                self.stop_backend(second)
                self.stop_backend(first)

    def test_stop_during_backend_startup(self):
        with TemporaryDirectory() as temp:
            backend = BackendProcess(db_path=Path(temp) / "auth.db", port=free_port())
            backend.start()
            self.stop_backend(backend)

    def test_network_failure_is_not_signup_required(self):
        client = AuthApiClient(f"http://127.0.0.1:{free_port()}", timeout_ms=300)
        results = []
        client.completed.connect(lambda *args: results.append(args))
        client.request("signup-status")
        wait_for(lambda: bool(results))
        self.assertEqual(results[0][2], "CONNECTION_ERROR")


if __name__ == "__main__":
    unittest.main()
