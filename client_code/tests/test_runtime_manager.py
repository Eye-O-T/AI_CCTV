"""단일 실행 관리자의 요청·종료·실패 경계를 장치 없이 검증한다."""
import unittest
from unittest.mock import MagicMock, patch

from test_camera_runtime import CAM, FakeWorker
from test_client_auth import APP
from cameras.runtime_manager import CameraRuntimeManager
from ui.settings_window import SettingsWindow


class RuntimeManagerTests(unittest.TestCase):
    def setUp(self):
        self.runtime = CameraRuntimeManager()
        self.runtime.detail_api = MagicMock()
        self.runtime.detail_api.request.return_value = True
        self.notices = []
        self.finished = []
        self.runtime.notice.connect(self.notices.append)
        self.runtime.stopped.connect(lambda: self.finished.append(True))

    def tearDown(self):
        self.runtime._stop_timer.stop()
        if self.runtime.worker is not None:
            self.runtime.worker.deleteLater()
        self.runtime.deleteLater()
        APP.processEvents()

    def launch(self, options=None):
        self.assertTrue(self.runtime.request_detail(CAM, 'start', options))
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            self.runtime.camera_detail_received(CAM, '', '')
        return self.runtime.worker

    def test_single_execution_guard_and_settings_snapshot(self):
        options = dict(use_yolo=False, use_vlm=True, clip_max_seconds=30)
        self.assertTrue(self.runtime.request_detail(CAM, 'start', options))
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        self.assertFalse(self.runtime.request_detail(CAM | {'camera_id': 8}, 'start'))
        options['clip_max_seconds'] = 10
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            self.runtime.camera_detail_received(CAM, '', '')
        worker = self.runtime.worker
        self.assertEqual(worker.values['clip_max_seconds'], 30)
        self.assertFalse(worker.values['use_yolo'])
        self.assertFalse(worker.values['use_vlm'])
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        self.runtime.stop()
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        worker.active = False
        self.runtime._poll_stop()
        self.assertFalse(self.runtime.busy)
        self.assertEqual(self.finished, [True])

    def test_rejected_request_invalid_detail_and_constructor_failure_release(self):
        self.runtime.detail_api.request.return_value = False
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        self.assertFalse(self.runtime.busy)
        self.runtime.detail_api.request.return_value = True
        for payload in (None, {}, CAM | {'camera_id': 8}, CAM | {'rtsp_url': None}):
            self.assertTrue(self.runtime.request_detail(CAM, 'start'))
            self.runtime.camera_detail_received(payload, '', '')
            self.assertIsNone(self.runtime.worker)
            self.assertFalse(self.runtime.busy)
        self.runtime.request_detail(CAM, 'start')
        with patch('workers.video_worker.VideoWorker', side_effect=RuntimeError('private input')):
            self.runtime.camera_detail_received(CAM, '', '')
        self.assertFalse(self.runtime.busy)
        self.assertIsNone(self.runtime.running_camera)
        self.assertEqual(self.runtime.runtime_states[7], '오류')
        self.assertNotIn('private input', str(self.notices))

    def test_old_worker_signals_cannot_change_restarted_run(self):
        old = self.launch()
        self.runtime.stop()
        old.active = False
        self.runtime._poll_stop()
        current = self.launch()
        received = []
        self.runtime.metrics_ready.connect(received.append)
        old.status_ready.emit('LIVE')
        old.metrics_ready.emit({'current_objects': 99})
        old.event_ready.emit({'type': 'network_failure'})
        old.finished.emit()
        self.assertEqual(self.runtime.runtime_states[7], '로딩 중')
        self.assertFalse(self.runtime.stopping)
        self.assertFalse(received)
        current.metrics_ready.emit({'current_objects': 2})
        self.assertEqual(received, [{'current_objects': 2}])

    def test_shutdown_cancels_detail_and_rejects_late_result_and_new_start(self):
        self.runtime.request_detail(CAM, 'start')
        self.runtime.begin_shutdown()
        self.runtime.detail_api.cancel.assert_called_once()
        with patch('workers.video_worker.VideoWorker') as factory:
            self.runtime.camera_detail_received(CAM, '', '')
            factory.assert_not_called()
        self.runtime._poll_stop()
        self.assertEqual(self.finished, [True])
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        self.runtime._poll_stop()
        self.assertEqual(self.finished, [True])

    def test_slow_cleanup_monitor_and_failure_never_report_success(self):
        worker = self.launch()
        self.runtime.monitor_pending = True
        self.runtime.begin_shutdown()
        self.runtime._stop_started -= 31
        self.runtime._poll_stop()
        self.assertIn('시간이 걸리고', self.notices[-1])
        worker.active = False
        self.runtime._poll_stop()
        self.assertFalse(self.finished)
        self.assertIs(self.runtime.worker, worker)
        self.runtime.monitor_pending = False
        worker.cleanup_ok = False
        self.runtime._poll_stop()
        self.assertFalse(self.finished)
        self.assertTrue(self.runtime.stopping)
        self.assertIn('실패', self.notices[-1])
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))

    def test_idle_monitor_detail_does_not_create_video_worker(self):
        monitors = []
        self.runtime.monitor_ready.connect(monitors.append)
        self.runtime.request_detail(CAM, 'monitor')
        with patch('workers.video_worker.VideoWorker') as factory:
            self.runtime.camera_detail_received(CAM, '', '')
            factory.assert_not_called()
        self.assertEqual(monitors, [CAM])
        self.assertFalse(self.runtime.busy)

    def test_vlm_setting_cannot_be_enabled(self):
        dialog = SettingsWindow(use_vlm=True)
        try:
            self.assertFalse(dialog.vlm_checkbox.isEnabled())
            self.assertFalse(dialog.vlm_checkbox.isChecked())
            dialog.yolo_checkbox.setChecked(False)
            dialog.yolo_checkbox.setChecked(True)
            self.assertFalse(dialog.vlm_checkbox.isEnabled())
            dialog.save_basic_settings()
            self.assertFalse(dialog.use_vlm)
        finally:
            dialog.deleteLater()
