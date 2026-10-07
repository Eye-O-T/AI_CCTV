"""두 슬롯의 요청·실행·취소·정리 격리 검증."""
import unittest
from unittest.mock import patch
from test_camera_runtime import CAM, FakeWorker, FakeCameraApi
from test_client_auth import APP
from cameras.runtime_manager import CameraRuntimeManager
from ui.settings_window import SettingsWindow

SECOND = CAM | {'camera_id': 8, 'name': '두 번째'}
THIRD = CAM | {'camera_id': 9, 'name': '세 번째'}


class RuntimeManagerTests(unittest.TestCase):
    def setUp(self):
        self.runtime = CameraRuntimeManager(api_factory=FakeCameraApi)
        self.runtime.bind_session(object())
        self.notices, self.finished, self.shutdowns = [], [], []
        self.runtime.notice.connect(lambda cid, text: self.notices.append((cid, text)))
        self.runtime.stopped.connect(self.finished.append)
        self.runtime.shutdown_complete.connect(lambda: self.shutdowns.append(True))

    def tearDown(self):
        self.runtime._stop_timer.stop()
        for run in self.runtime.runs.values():
            if run.worker is not None:
                run.worker.deleteLater()
        self.runtime.deleteLater()
        APP.processEvents()

    def request(self, camera=CAM, options=None):
        self.assertTrue(self.runtime.request_detail(camera, 'start', options))
        return self.runtime.runs[camera['camera_id']]

    def launch(self, camera=CAM, options=None):
        run = self.request(camera, options)
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            run.api.completed.emit(camera, '', '')
        return run

    def test_two_slots_duplicate_limit_and_settings_snapshot(self):
        options = dict(use_yolo=False, use_vlm=True, clip_max_seconds=30)
        first = self.request(options=options)
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        second = self.request(SECOND)
        self.assertFalse(self.runtime.request_detail(THIRD, 'start'))
        options['clip_max_seconds'] = 10
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            first.api.completed.emit(CAM, '', '')
            second.api.completed.emit(SECOND, '', '')
        self.assertEqual(first.worker.values['clip_max_seconds'], 30)
        self.assertFalse(first.worker.values['use_yolo'])
        self.assertFalse(first.worker.values['use_vlm'])
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        self.runtime.stop(7)
        self.assertFalse(self.runtime.request_detail(THIRD, 'start'))
        first.worker.active = False
        self.runtime._poll_stop()
        self.assertTrue(second.worker.active)
        self.assertFalse(second.worker.stop_requested)
        third = self.request(THIRD)
        self.assertEqual(third.slot, 0)
        self.assertEqual(second.slot, 1)

    def test_request_rejection_invalid_detail_and_constructor_failure_release(self):
        class RejectApi(FakeCameraApi):
            def __init__(self, *args):
                super().__init__(*args)
                self.request.return_value = False
        self.runtime.api_factory = RejectApi
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        self.assertFalse(self.runtime.busy)
        self.runtime.api_factory = FakeCameraApi
        for payload in (None, {}, SECOND, CAM | {'rtsp_url': None}):
            run = self.request()
            run.api.completed.emit(payload, '', '')
            self.assertFalse(self.runtime.busy)
        run = self.request()
        with patch('workers.video_worker.VideoWorker', side_effect=RuntimeError('private input')):
            run.api.completed.emit(CAM, '', '')
        self.assertFalse(self.runtime.busy)
        self.assertEqual(self.runtime.runtime_states[7], '오류')
        self.assertNotIn('private input', str(self.notices))

    def test_cancel_one_detail_and_late_result_after_restart(self):
        first = self.request()
        second = self.request(SECOND)
        self.runtime.stop(7)
        self.runtime._poll_stop()
        second.api.cancel.assert_not_called()
        replacement = self.request()
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            first.api.completed.emit(CAM, '', '')
            self.assertIsNone(replacement.worker)
            replacement.api.completed.emit(CAM, '', '')
            second.api.completed.emit(SECOND, '', '')
        self.assertIsNotNone(second.worker)
        self.assertIsNotNone(replacement.worker)

    def test_old_worker_signals_cannot_change_restarted_run(self):
        old = self.launch()
        self.runtime.stop(7)
        old.worker.active = False
        self.runtime._poll_stop()
        current = self.launch()
        received = []
        self.runtime.metrics_ready.connect(lambda cid, data: received.append((cid, data)))
        old.worker.status_ready.emit('LIVE')
        old.worker.metrics_ready.emit({'current_objects': 99})
        old.worker.event_ready.emit({'type': 'network_failure'})
        old.worker.finished.emit()
        self.assertEqual(self.runtime.runtime_states[7], '로딩 중')
        self.assertFalse(current.stopping)
        self.assertFalse(received)
        current.worker.metrics_ready.emit({'current_objects': 2})
        self.assertEqual(received, [(7, {'current_objects': 2})])
        self.assertNotEqual(old.generation, current.generation)

    def test_shutdown_cancels_both_details_and_blocks_late_results(self):
        first, second = self.request(), self.request(SECOND)
        self.runtime.begin_shutdown()
        first.api.cancel.assert_called_once()
        second.api.cancel.assert_called_once()
        with patch('workers.video_worker.VideoWorker') as factory:
            first.api.completed.emit(CAM, '', '')
            second.api.completed.emit(SECOND, '', '')
            factory.assert_not_called()
        self.runtime._poll_stop()
        self.assertEqual(self.shutdowns, [True])
        self.assertFalse(self.runtime.request_detail(CAM, 'start'))
        self.runtime._poll_stop()
        self.assertEqual(self.shutdowns, [True])

    def test_cleanup_failure_does_not_block_other_stop_or_report_shutdown(self):
        first, second = self.launch(), self.launch(SECOND)
        self.runtime.stop(7)
        first.stop_started -= 31
        self.runtime._poll_stop()
        self.assertIn('시간이 걸리고', self.notices[-1][1])
        first.worker.active = False
        first.worker.cleanup_ok = False
        self.runtime._poll_stop()
        self.assertTrue(first.cleanup_failed)
        self.assertFalse(self.runtime.can_start(THIRD))
        self.runtime.stop(8)
        second.worker.active = False
        self.runtime._poll_stop()
        self.assertNotIn(8, self.runtime.runs)
        self.assertIn(7, self.runtime.runs)
        self.assertTrue(self.runtime.can_start(THIRD))
        self.runtime.begin_shutdown()
        self.runtime._poll_stop()
        self.assertFalse(self.shutdowns)

    def test_monitor_wait_only_blocks_its_camera_and_shutdown(self):
        first, second = self.launch(), self.launch(SECOND)
        self.assertTrue(self.runtime.request_detail(CAM, 'monitor'))
        self.runtime.stop(8)
        second.worker.active = False
        self.runtime._poll_stop()
        self.assertNotIn(8, self.runtime.runs)
        self.runtime.stop(7)
        first.worker.active = False
        self.runtime._poll_stop()
        self.assertIn(7, self.runtime.runs)
        self.runtime.begin_shutdown()
        self.assertFalse(self.shutdowns)
        self.runtime.monitor_closed()
        self.runtime._poll_stop()
        self.assertEqual(self.shutdowns, [True])

    def test_idle_monitor_does_not_use_execution_slot(self):
        monitors = []
        self.runtime.monitor_ready.connect(monitors.append)
        self.runtime.request_detail(CAM, 'monitor')
        self.assertEqual(self.runtime.occupied_slots, 0)
        with patch('workers.video_worker.VideoWorker') as factory:
            self.runtime.runs[7].api.completed.emit(CAM, '', '')
            factory.assert_not_called()
        self.assertEqual(monitors, [CAM])
        self.assertTrue(self.runtime.busy)
        self.request(SECOND)
        self.request(THIRD)
        self.assertEqual(self.runtime.occupied_slots, 2)
        self.assertFalse(self.runtime.request_detail(CAM, 'monitor'))

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
