"""두 실행의 화면 전환과 AI OFF 합성 영상 수신·저장 검증."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PyQt5.QtCore import QThread
from PyQt5.QtTest import QTest
from test_camera_runtime import CAM, FakeCameraApi, FakeWorker
from test_client_auth import APP, wait_for
from ui.gui import CCTVMainWindow
from workers.video_worker import VideoWorker

SECOND = CAM | {'camera_id': 8, 'name': '정문'}
THIRD = CAM | {'camera_id': 9, 'name': '세 번째'}


class MultiCameraWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = CCTVMainWindow()
        self.window.runtime.api_factory = FakeCameraApi
        self.window.runtime.bind_session(object())
        self.window.use_yolo = False
        self.window.cameras_received({'items': [CAM, SECOND, THIRD]}, '', '')
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.runtime._stop_timer.stop()
        for run in self.window.runtime.runs.values():
            if run.worker is not None:
                run.worker.deleteLater()
        self.window.allow_close = True
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def launch(self, row, camera):
        self.window.camera_list.setCurrentRow(row)
        self.window.start_video()
        run = self.window.runtime.runs[camera['camera_id']]
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            run.api.completed.emit(camera, '', '')
        return run

    def test_views_switch_without_restart_and_keep_hidden_frames_separate(self):
        first, second = self.launch(1, CAM), self.launch(2, SECOND)
        views = dict(self.window.views)
        red = np.full((90, 160, 3), (0, 0, 255), dtype=np.uint8)
        blue = np.full((90, 160, 3), (255, 0, 0), dtype=np.uint8)
        first.worker.frame_ready.emit(red)
        second.worker.frame_ready.emit(blue)
        red[:] = 0
        first.worker.metrics_ready.emit({'current_objects': 2})
        second.worker.metrics_ready.emit({'current_objects': 3})
        for row in (0, 1, 2, 0, 3, 1):
            self.window.camera_list.setCurrentRow(row)
            APP.processEvents()
            self.assertIs(self.window.runtime.runs[7], first)
            self.assertIs(self.window.runtime.runs[8], second)
            self.assertIs(self.window.views[7], views[7])
            self.assertIs(self.window.views[8], views[8])
            self.assertFalse(first.worker.stop_requested)
            self.assertFalse(second.worker.stop_requested)
            self.assertEqual(self.window.video_grid.count(), 4 if row == 0 else 1)
        self.assertEqual(views[7].pixmap.toImage().pixelColor(0, 0).red(), 255)
        self.assertEqual(views[8].pixmap.toImage().pixelColor(0, 0).blue(), 255)
        self.assertIn('2', views[7].current.text())
        self.assertIn('3', views[8].current.text())
        second.worker.metrics_ready.emit({'current_objects': 4})
        self.assertIn('4', views[8].current.text())
        self.assertFalse(self.window.btn_setting.isEnabled())
        self.window.camera_list.setCurrentRow(3)
        self.assertFalse(self.window.btn_start.isEnabled())
        self.window.camera_list.setCurrentRow(0)
        self.assertFalse(self.window.btn_start.isEnabled())
        self.assertFalse(self.window.btn_stop.isEnabled())
        self.assertFalse(self.window.btn_resource_monitor.isEnabled())

    def test_pending_selection_change_individual_stop_and_stable_slots(self):
        self.window.camera_list.setCurrentRow(1)
        self.window.start_video()
        first = self.window.runtime.runs[7]
        self.window.camera_list.setCurrentRow(2)
        self.window.start_video()
        second = self.window.runtime.runs[8]
        self.window.camera_list.setCurrentRow(0)
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            second.api.completed.emit(SECOND, '', '')
            first.api.completed.emit(CAM, '', '')
        self.assertEqual(first.worker.values['camera_id'], 7)
        self.assertEqual(second.worker.values['camera_id'], 8)
        first.worker.status_ready.emit('연결 대기 / 재연결 중')
        self.assertEqual(self.window.runtime.slots, [7, 8])
        self.window.camera_list.setCurrentRow(1)
        self.window.stop_video()
        first.worker.active = False
        self.window.runtime._poll_stop()
        self.assertFalse(second.worker.stop_requested)
        third = self.launch(3, THIRD)
        self.assertEqual(self.window.runtime.slots, [9, 8])
        self.assertEqual(third.slot, 0)

    def test_event_cards_absent_but_camera_errors_and_reconnect_still_visible(self):
        first, second = self.launch(1, CAM), self.launch(2, SECOND)
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        first.worker.frame_ready.emit(frame)
        second.worker.frame_ready.emit(frame)
        for kind in ('appear', 'disappear', 'analysis'):
            first.worker.event_ready.emit({'type': kind, 'person_id': 1})
        self.assertFalse(hasattr(self.window, 'event_list'))
        self.assertFalse(hasattr(self.window, 'appear_count'))
        first.worker.event_ready.emit({'type': 'error', 'message': 'AI 분석 중지'})
        self.assertEqual(self.window.views[7].status.text(), 'LIVE')
        self.assertIn('AI 분석 중지', self.window.views[7].message.text())
        first.worker.event_ready.emit({'type': 'network_failure'})
        self.assertIsNone(self.window.views[7].pixmap)
        self.assertIsNotNone(self.window.views[8].pixmap)
        first.worker.event_ready.emit({'type': 'network_recovered'})
        self.assertNotEqual(self.window.views[7].status.text(), 'LIVE')
        first.worker.frame_ready.emit(frame)
        self.assertEqual(self.window.views[7].status.text(), 'LIVE')

    def test_monitor_keeps_original_target_and_unrelated_stop_continues(self):
        first, second = self.launch(1, CAM), self.launch(2, SECOND)
        self.window.camera_list.setCurrentRow(1)
        with patch('ui.gui.ResourceMonitorWindow') as factory:
            self.window.open_resource_monitor()
            monitor = factory.return_value
            self.window.camera_list.setCurrentRow(2)
            self.assertFalse(self.window.btn_resource_monitor.isEnabled())
            self.window.stop_video()
            second.worker.active = False
            self.window.runtime._poll_stop()
            monitor.close.assert_not_called()
            self.assertEqual(self.window.runtime.monitor_camera_id, 7)
            self.assertEqual(factory.call_args.kwargs['resource_server_url'], CAM['edge_api_base_url'])
            self.window.camera_list.setCurrentRow(1)
            self.window.stop_video()
            monitor.close.assert_called_once()
            first.worker.active = False
            self.window.runtime._poll_stop()
            self.assertIn(7, self.window.runtime.runs)
            self.window.handle_resource_monitor_closed()
            self.window.runtime._poll_stop()
            self.assertFalse(self.window.runtime.runs)

    def test_storage_failure_during_stop_is_visible_for_its_camera_only(self):
        first, second = self.launch(1, CAM), self.launch(2, SECOND)
        second.worker.frame_ready.emit(np.zeros((48, 64, 3), np.uint8))
        self.window.runtime.stop(7)
        first.worker.storage_errors = ['장애 복구 영상 저장 실패']
        first.worker.active = False
        self.window.runtime._poll_stop()
        self.assertIn('저장 실패', self.window.views[7].message.text())
        self.assertEqual(self.window.views[8].status.text(), 'LIVE')
        self.assertNotIn('저장 실패', self.window.views[8].message.text())
        self.assertFalse(second.worker.stop_requested)

    def test_resize_keeps_aspect_ratio_and_four_cells(self):
        first, second = self.launch(1, CAM), self.launch(2, SECOND)
        for run in (first, second):
            run.worker.frame_ready.emit(np.zeros((90, 160, 3), dtype=np.uint8))
        for row, width, height in ((0, 1280, 720), (1, 1400, 820), (0, 1600, 900)):
            self.window.camera_list.setCurrentRow(row)
            self.window.resize(width, height)
            QTest.qWait(30)
            for camera_id in ((7, 8) if row == 0 else (7,)):
                video = self.window.views[camera_id].video
                pixmap = video.pixmap()
                self.assertLessEqual(pixmap.width(), video.width())
                self.assertLessEqual(pixmap.height(), video.height())
                self.assertAlmostEqual(pixmap.width() / pixmap.height(), 16 / 9, delta=0.03)
            self.assertEqual(self.window.video_grid.count(), 4 if row == 0 else 1)


class SyntheticMultiCameraTests(unittest.TestCase):
    def test_two_opencv_sources_keep_running_offscreen_stop_restart_and_record(self):
        self._run_sources(False)

    def test_two_fake_models_with_real_opencv_storage_stop_and_restart(self):
        import sys
        from types import SimpleNamespace
        from test_tracking_storage import FakeYOLO
        FakeYOLO.instances = []
        with patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=FakeYOLO)}):
            self._run_sources(True)
        self.assertEqual(len(FakeYOLO.instances), 3)
        self.assertTrue(all(model.calls >= 5 for model in FakeYOLO.instances))

    def _run_sources(self, use_yolo):
        with TemporaryDirectory() as temp:
            paths = {}
            for camera_id, color in ((7, 60), (8, 180)):
                path = str(Path(temp) / f'{camera_id}.avi')
                writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'MJPG'), 30, (64, 48))
                self.assertTrue(writer.isOpened())
                for _ in range(300):
                    writer.write(np.full((48, 64, 3), color, dtype=np.uint8))
                writer.release()
                paths[camera_id] = path
            window = CCTVMainWindow()
            window.runtime.api_factory = FakeCameraApi
            window.runtime.bind_session(object())
            window.use_yolo = use_yolo
            window.ai_cctv_path = temp
            window.cameras_received({'items': [CAM, SECOND]}, '', '')
            counts = {7: 0, 8: 0}
            threads, workers = [], []

            def frame_received(camera_id, frame):
                counts[camera_id] += 1
                threads.append(QThread.currentThread() == APP.thread())

            def factory(**kwargs):
                kwargs['source'] = paths[kwargs['camera_id']]
                kwargs['edge_api_base_url'] = None
                worker = VideoWorker(**kwargs)
                read = worker.stream.read

                def paced_read():
                    time.sleep(0.01)
                    return read()

                worker.stream.read = paced_read
                workers.append(worker)
                return worker

            window.runtime.frame_ready.connect(frame_received)
            try:
                with patch('workers.video_worker.VideoWorker', side_effect=factory):
                    for row, camera in ((1, CAM), (2, SECOND)):
                        window.camera_list.setCurrentRow(row)
                        window.start_video()
                        window.runtime.runs[camera['camera_id']].api.completed.emit(camera, '', '')
                    wait_for(lambda: min(counts.values()) >= 8)
                    window.camera_list.setCurrentRow(1)
                    hidden_count = counts[8]
                    wait_for(lambda: counts[8] >= hidden_count + 5)
                    window.stop_video()
                    wait_for(lambda: 7 not in window.runtime.runs)
                    before = counts[8]
                    wait_for(lambda: counts[8] >= before + 5)
                    window.start_video()
                    window.runtime.runs[7].api.completed.emit(CAM, '', '')
                    before = counts[7]
                    wait_for(lambda: counts[7] >= before + 5)
                    self.assertEqual(window.runtime.occupied_slots, 2)
                window.begin_shutdown()
                wait_for(lambda: not window.runtime.busy)
                self.assertTrue(all(threads))
                self.assertEqual(len({w.execution_id for w in workers}), 3)
                self.assertTrue(all(w.cleanup_ok and w.use_yolo == use_yolo and w.vlm_worker is None for w in workers))
                for worker in workers:
                    if use_yolo:
                        self.assertEqual(len(list(worker.run_directory.rglob('preview.mp4'))), 2)
                        self.assertEqual(len(list(worker.run_directory.rglob('trajectory.jpg'))), 2)
                        self.assertFalse(list(worker.run_directory.rglob('full_crop.jpg')))
                        self.assertIsNone(worker.tracker)
                    videos = list(worker.run_directory.rglob('*.mp4'))
                    self.assertTrue(videos)
                    for video in videos:
                        capture = cv2.VideoCapture(str(video))
                        try:
                            ok, saved = capture.read()
                            self.assertTrue(ok)
                            self.assertAlmostEqual(float(saved.mean()), 60 if worker.camera_id == 7 else 180, delta=8)
                        finally:
                            capture.release()
                    for trajectory in worker.run_directory.rglob('trajectory.jpg'):
                        saved = cv2.imdecode(np.fromfile(trajectory, dtype=np.uint8), cv2.IMREAD_COLOR)
                        self.assertEqual(saved.shape, (48, 64, 3))
            finally:
                window.begin_shutdown()
                wait_for(lambda: not window.runtime.busy)
                window.allow_close = True
                window.close()
                window.deleteLater()
                APP.processEvents()
