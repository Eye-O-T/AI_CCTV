"""장치 없이 실행 연결·늦은 신호·종료와 합성 영상 수신을 검증한다."""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal
from test_client_auth import APP, wait_for
from ui.gui import CCTVMainWindow
from workers.video_worker import VideoWorker
from video.video_stream import VideoStream
from video.rtsp_receiver import RTSPReceiver, check_server_port_open

CAM = dict(camera_id=7, name='후문', camera_type='edge_node', is_enabled=True,
           webcam_index=None, rtsp_url='rtsp://192.0.2.10:8554/live', edge_api_base_url='http://192.0.2.11:9000/api')


class FakeWorker(QObject):
    frame_ready = pyqtSignal(object)
    metrics_ready = pyqtSignal(dict)
    event_ready = pyqtSignal(dict)
    loading_ready = pyqtSignal(str)
    status_ready = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, **kwargs):
        super().__init__()
        self.values = kwargs
        self.active = False
        self.cleanup_ok = True
        self.stop_requested = False

    def start(self):
        self.active = True

    def request_stop(self):
        self.stop_requested = True

    def isRunning(self):
        return self.active


class RuntimeWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = CCTVMainWindow()
        self.window.detail_api = MagicMock()
        self.window.detail_api.request.return_value = True
        self.window.cameras_received({'items': [CAM]}, '', '')
        self.window.camera_list.setCurrentRow(0)

    def tearDown(self):
        self.window._stop_timer.stop()
        self.window.allow_close = True
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def launch(self):
        self.window.start_video()
        with patch('workers.video_worker.VideoWorker', FakeWorker):
            self.window.camera_detail_received(CAM, '', '')
        return self.window.worker

    def test_fresh_detail_start_stop_and_late_signals(self):
        worker = self.launch()
        self.window.detail_api.request.assert_called_with('GET', '/7')
        self.assertEqual(worker.values['camera_id'], 7)
        self.assertEqual(worker.values['edge_api_base_url'], CAM['edge_api_base_url'])
        self.assertEqual(worker.values['source'], CAM['rtsp_url'])
        self.assertFalse(self.window.camera_list.isEnabled())
        self.assertFalse(self.window.btn_setting.isEnabled())
        self.assertFalse(self.window.btn_start.isEnabled())
        worker.frame_ready.emit(np.zeros((48, 64, 3), dtype=np.uint8))
        self.assertEqual(self.window.runtime_states[7], 'LIVE')
        worker.event_ready.emit(dict(type='error', message='AI 분석 중지'))
        self.assertEqual(self.window.runtime_states[7], 'LIVE')
        worker.event_ready.emit(dict(type='network_failure'))
        worker.event_ready.emit(dict(type='network_recovered'))
        self.assertEqual(self.window.runtime_states[7], '연결 장애 / 재연결 중')
        generation = self.window.run_generation
        self.window.stop_video()
        self.assertTrue(worker.stop_requested)
        worker.frame_ready.emit(np.zeros((48, 64, 3), dtype=np.uint8))
        self.assertEqual(self.window.runtime_states[7], '중지 중')
        worker.active = False
        self.window._poll_stop()
        self.assertIsNone(self.window.worker)
        self.assertEqual(self.window.runtime_states[7], '중지됨')
        self.assertTrue(self.window.camera_list.isEnabled())
        self.window._dispatch_video(generation, self.window._runtime_status, 'LIVE')
        self.assertEqual(self.window.runtime_states[7], '중지됨')

    def test_disabled_latest_and_cancelled_detail_never_start(self):
        self.window.start_video()
        self.window.camera_detail_received(CAM | {'is_enabled': False}, '', '')
        self.assertIsNone(self.window.worker)
        self.assertFalse(self.window.btn_start.isEnabled())
        self.window.camera_rows[7] = CAM
        self.window.start_video()
        self.window.stop_video()
        self.window.camera_detail_received(CAM, '', '')
        self.assertIsNone(self.window.worker)
        self.window._poll_stop()

    def test_detail_error_and_unexpected_worker_finish(self):
        self.window.start_video()
        self.window.camera_detail_received(None, 'CONNECTION_ERROR', '')
        self.assertIsNone(self.window.worker)
        self.assertTrue(self.window.btn_start.isEnabled())
        worker = self.launch()
        worker.active = False
        worker.finished.emit()
        self.window._poll_stop()
        self.assertEqual(self.window.runtime_states[7], '오류')
        self.assertIsNone(self.window.worker)

    def test_monitor_explicit_url_and_stop_waits(self):
        self.launch()
        with patch('ui.gui.ResourceMonitorWindow') as monitor:
            self.window.open_resource_monitor()
            self.assertEqual(monitor.call_args.kwargs['resource_server_url'], CAM['edge_api_base_url'])
        self.window.stop_video()
        self.window.worker.active = False
        self.window._poll_stop()
        self.assertIsNotNone(self.window.worker)
        self.window.resource_monitor_window = None
        self.window._poll_stop()
        self.assertIsNone(self.window.worker)


class VideoRuntimeTests(unittest.TestCase):
    def test_storage_isolation_and_recovery_destination(self):
        with TemporaryDirectory() as temp:
            first = VideoWorker(camera_id=7, ai_cctv_path=temp, use_yolo=False, edge_api_base_url=CAM['edge_api_base_url'])
            second = VideoWorker(camera_id=7, ai_cctv_path=temp, use_yolo=False)
            third = VideoWorker(camera_id=8, ai_cctv_path=temp, use_yolo=False)
            self.assertEqual(len({first.run_directory, second.run_directory, third.run_directory}), 3)
            self.assertTrue(first.run_directory.is_relative_to(Path(temp)))
            self.assertIn('camera_7', str(first.run_directory))
            first.stream = MagicMock(is_rtsp=True)
            first.stream.open.return_value = True
            first.stream.get_fps.return_value = 15
            first.stream.read.side_effect = lambda: (setattr(first, 'running', False) or False, None)
            first.stream.pop_connection_events.return_value = []
            with patch('workers.video_worker.NetworkRecoveryManager') as recovery, patch('workers.video_worker.RecordingManager'):
                first.run()
                self.assertEqual(recovery.call_args.kwargs['server_url'], CAM['edge_api_base_url'] + '/recover')
                self.assertEqual(recovery.call_args.kwargs['camera_id'], 'camera_7')
                self.assertEqual(recovery.call_args.kwargs['base_dir'], first.ai_cctv_path)
            self.assertTrue(first.cleanup_ok)

    def test_no_edge_api_no_recovery(self):
        with patch('workers.video_worker.CropManager'):
            worker = VideoWorker(camera_id=2, use_yolo=False, ai_cctv_path='unused-test-root')
        worker.stream = MagicMock(is_rtsp=True)
        worker.stream.open.return_value = True
        worker.stream.get_fps.return_value = 15
        worker.stream.pop_connection_events.return_value = []
        worker.stream.read.side_effect = lambda: (setattr(worker, 'running', False) or False, None)
        with patch('workers.video_worker.NetworkRecoveryManager') as recovery, patch('workers.video_worker.RecordingManager'):
            worker.run()
            recovery.assert_not_called()

    def test_rtsps_explicit_port_and_no_stale_frame(self):
        self.assertTrue(VideoStream('rtsps://example.test/live').is_rtsp)
        with patch('video.rtsp_receiver.socket.create_connection') as connect:
            self.assertTrue(check_server_port_open('rtsps://example.test/live'))
            connect.assert_not_called()
            self.assertTrue(check_server_port_open('rtsp://[::1]:8554/live'))
            connect.assert_called_with(('::1', 8554), timeout=1.5)
        receiver = RTSPReceiver('rtsp://example.test/live')
        receiver.frame = np.zeros((8, 8, 3), dtype=np.uint8)
        receiver.is_connected = False
        self.assertIsNone(receiver.get_frame())

    def test_real_opencv_synthetic_video_and_recording(self):
        with TemporaryDirectory() as temp:
            source = str(Path(temp) / 'input.avi')
            writer = cv2.VideoWriter(source, cv2.VideoWriter_fourcc(*'MJPG'), 15, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(6):
                writer.write(np.full((48, 64, 3), i * 30, dtype=np.uint8))
            writer.release()
            worker = VideoWorker(source=source, camera_id=9, use_yolo=False, use_vlm=False, ai_cctv_path=temp)
            frames = []
            worker.frame_ready.connect(frames.append)
            worker.start()
            wait_for(lambda: not worker.isRunning(), seconds=10)
            APP.processEvents()
            self.assertEqual(len(frames), 6)
            self.assertTrue(worker.cleanup_ok)
            self.assertTrue(list(worker.run_directory.rglob('*.mp4')))
            worker.deleteLater()


if __name__ == '__main__':
    unittest.main()
