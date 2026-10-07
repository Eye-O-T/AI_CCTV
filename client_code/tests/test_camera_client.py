"""PyQt 카메라 관리: 실제 임시 백엔드와 offscreen 화면 검증."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt, QProcess
from PyQt5.QtTest import QTest
from auth.api_client import AuthApiClient
from auth.backend_process import BackendProcess
from auth.session_manager import SessionManager
from cameras.api_client import CameraApiClient
from ui.camera_management import CameraManagementPage
from ui.settings_window import SettingsWindow
from ui.gui import CCTVMainWindow
from test_client_auth import APP, wait_for, free_port


class CameraClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.port = free_port()
        cls.backend = BackendProcess(db_path=Path(cls.tmp.name) / 'camera.db', port=cls.port)
        cls.backend.start()
        wait_for(lambda: cls.backend.is_ready, seconds=20)
        cls.session = SessionManager(AuthApiClient(f'http://127.0.0.1:{cls.port}'))
        cls.session.signup('admin', 'test-pass', 'test-pass')
        wait_for(lambda: not cls.session.busy)
        cls.session.login('admin', 'test-pass')
        wait_for(lambda: cls.session.access_token is not None)

    @classmethod
    def tearDownClass(cls):
        cls.session.logout()
        wait_for(lambda: cls.session.access_token is None)
        cls.backend.stop()
        wait_for(lambda: cls.backend.process.state() == QProcess.NotRunning, seconds=15)
        cls.tmp.cleanup()

    def test_01_management_save_edit_failure_and_main_refresh(self):
        window = CCTVMainWindow()
        window.bind_camera_session(self.session)
        self.addCleanup(window.camera_api.cancel)
        self.addCleanup(window.deleteLater)
        wait_for(lambda: window.camera_api.pending is None)
        self.assertEqual(window.camera_list.count(), 1)
        self.assertIn('등록된 카메라가 없습니다', window.cam_status.text())
        page = CameraManagementPage(self.session)
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close_requests)
        page.saved.connect(window.camera_saved)
        wait_for(lambda: page.api.pending is None)
        page.name.setText('정문')
        page.save()
        wait_for(lambda: page.api.pending is None and page.original is not None and window.camera_list.count() == 2)
        self.assertEqual(page.original['name'], '정문')
        self.assertFalse(page.kind.isEnabled())
        self.assertEqual(window.selected_camera_id, page.camera_id)
        self.assertIn('정문', window.center_title.text())
        self.assertTrue(window.btn_start.isEnabled())
        self.assertFalse(window.runtime.runs)
        window._set_controls(True)
        self.assertTrue(window.btn_start.isEnabled())
        page.enabled.setChecked(False)
        page.save()
        wait_for(lambda: page.api.pending is None and not page.original['is_enabled'] and '비활성' in window.camera_list.item(1).text())
        page.new_camera()
        page.name.setText('중복 입력')
        page.save()
        wait_for(lambda: page.api.pending is None)
        self.assertIn('이미 등록', page.message.text())
        self.assertEqual(page.name.text(), '중복 입력')
        self.assertIsNone(page.camera_id)
        page.kind.setCurrentIndex(1)
        page.rtsp.setText('rtsp://192.0.2.10:8554/live')
        page.edge_api.setText('http://192.0.2.10:8002')
        page.save()
        wait_for(lambda: page.api.pending is None and page.original is not None and window.camera_list.count() == 3)
        page.edge_api.clear()
        page.save()
        wait_for(lambda: page.api.pending is None and page.original['edge_api_base_url'] is None)
        dialog = SettingsWindow(session=self.session)
        self.addCleanup(dialog.deleteLater)
        wait_for(lambda: dialog.camera_page.api.pending is None)
        self.assertEqual(dialog.btn_basic.text(), '분석 설정')
        self.assertFalse(hasattr(dialog, 'radio_webcam'))
        dialog.reject()
        self.assertIsNone(dialog.camera_page.api.pending)

    def test_02_two_401_requests_share_one_refresh(self):
        self.session.access_token = 'expired-test-token'
        first = CameraApiClient(self.session)
        second = CameraApiClient(self.session)
        self.addCleanup(first.deleteLater)
        self.addCleanup(second.deleteLater)
        results = []
        first.completed.connect(lambda p, e, f: results.append(e))
        second.completed.connect(lambda p, e, f: results.append(e))
        with patch.object(self.session.api, 'request', wraps=self.session.api.request) as request:
            first.request('GET')
            second.request('GET')
            wait_for(lambda: len(results) == 2)
            self.assertEqual(results, ['', ''])
            self.assertEqual(sum(c.args[0] == 'refresh' for c in request.call_args_list), 1)

    def test_03_cancel_network_failure_and_stale_response(self):
        api = CameraApiClient(self.session)
        self.addCleanup(api.deleteLater)
        results = []
        api.completed.connect(lambda p, e, f: results.append(e))
        api.request('GET')
        api.cancel()
        QTest.qWait(150)
        self.assertEqual(results, [])
        api.base_url = f'http://127.0.0.1:{free_port()}'
        api.request('GET')
        wait_for(lambda: bool(results))
        self.assertEqual(results, ['CONNECTION_ERROR'])

    def test_04_invalid_response_keeps_existing_list(self):
        window = CCTVMainWindow()
        self.addCleanup(window.deleteLater)
        row = dict(camera_id=1, name='카메라', camera_type='webcam', is_enabled=True)
        window.cameras_received({'items': [row]}, '', '')
        window.cameras_received({'items': [{'name': 'broken'}]}, '', '')
        self.assertEqual(window.camera_list.count(), 2)
        self.assertIn('응답 형식', window.cam_status.text())
        window.cameras_received(None, 'CONNECTION_ERROR', '')
        self.assertEqual(window.camera_list.count(), 2)
        self.assertNotIn('등록된 카메라가 없습니다', window.cam_status.text())

    def test_05_refresh_rejection_expires_session(self):
        # 이 테스트만의 로그인 세션을 만료시켜 공유 테스트 세션은 유지한다.
        session = SessionManager(AuthApiClient(f'http://127.0.0.1:{self.port}'))
        session.login('admin', 'test-pass')
        wait_for(lambda: session.access_token is not None)
        api = CameraApiClient(session)
        self.addCleanup(api.deleteLater)
        self.addCleanup(session.deleteLater)
        expired = []
        session.expired.connect(expired.append)
        session.access_token = 'invalid-access'
        session.refresh_token = 'invalid-refresh'
        api.request('GET')
        wait_for(lambda: bool(expired))
        self.assertTrue(session.expiry_reported)
        self.assertIsNone(api.pending)

    def test_06_runtime_two_details_share_refresh_and_start_independently(self):
        from test_camera_runtime import FakeWorker
        api = CameraApiClient(self.session)
        self.addCleanup(api.deleteLater)
        rows = []
        api.completed.connect(lambda p, e, f: rows.append(p))
        for index in (20, 21):
            api.request('POST', body=dict(name=f'parallel {index}', camera_type='webcam', webcam_index=index))
            wait_for(lambda: api.pending is None)
        self.assertEqual(len(rows), 2)
        window = CCTVMainWindow()
        self.addCleanup(window.deleteLater)
        window.bind_camera_session(self.session)
        self.addCleanup(window.camera_api.cancel)
        wait_for(lambda: window.camera_api.pending is None)
        self.session.access_token = 'expired-for-two-runtime-details'
        with patch.object(self.session.api, 'request', wraps=self.session.api.request) as request:
            with patch('workers.video_worker.VideoWorker', FakeWorker):
                for camera in rows:
                    self.assertTrue(window.runtime.request_detail(camera, 'start', {'use_yolo': False}))
                wait_for(lambda: all(run.worker is not None for run in window.runtime.runs.values()))
            self.assertEqual(sum(call.args[0] == 'refresh' for call in request.call_args_list), 1)
        self.assertEqual(window.runtime.occupied_slots, 2)
        window.begin_shutdown()
        for run in window.runtime.runs.values():
            self.assertTrue(run.worker.stop_requested)
            run.worker.active = False
        wait_for(lambda: not window.runtime.busy)


if __name__ == '__main__':
    unittest.main()
