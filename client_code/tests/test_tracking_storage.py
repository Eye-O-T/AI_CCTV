"""Stage 3: fake inference, real tracking/storage, and injected failures; no devices."""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import test_camera_runtime  # Initialize the shared offscreen Qt test environment.
from detection.person_tracker import PersonTracker, StablePersonIdMapper
from storage.clip_manager import ClipManager
from storage.recording_manager import RecordingManager
from recovery.network_recovery_manager import NetworkRecoveryManager
from workers.video_worker import VideoWorker


class FakeYOLO:
    instances = []

    def __init__(self, path):
        self.names = {0: 'person'}
        self.calls = 0
        self.callbacks = {}
        self.predictor = SimpleNamespace(trackers=[MagicMock()])
        self.instances.append(self)

    def add_callback(self, name, callback):
        self.callbacks[name] = callback

    def track(self, frame, **kwargs):
        self.calls += 1
        boxes = [SimpleNamespace(cls=[0], conf=[0.9], id=[i],
                                 xyxy=[[5 + 25 * i, 5, 20 + 25 * i, 40]]) for i in (0, 1)]
        return [SimpleNamespace(boxes=boxes)]


class TrackingStorageTests(unittest.TestCase):
    def test_real_bytetrack_ids_survive_other_camera_creation_and_reset(self):
        from detection.isolated_byte_tracker import IsolatedBYTETracker
        from ultralytics.engine.results import Boxes
        from ultralytics.utils import IterableSimpleNamespace
        args = IterableSimpleNamespace(track_buffer=30, track_high_thresh=0.5,
                                       track_low_thresh=0.1, new_track_thresh=0.6,
                                       match_thresh=0.8, fuse_score=True)
        boxes = Boxes(np.array([[5, 5, 20, 40, .9, 0]], dtype=np.float32), (48, 64))
        first = IsolatedBYTETracker(args)
        self.assertEqual(int(first.update(boxes)[0, 4]), 1)
        second = IsolatedBYTETracker(args)
        self.assertEqual(int(second.update(boxes)[0, 4]), 1)
        second.reset()
        self.assertEqual(first._next_track_id(), 2)
        self.assertEqual(int(second.update(boxes)[0, 4]), 2)
        self.assertEqual(len(first.tracked_stracks), 1)

    def test_model_callback_installs_separate_trackers_before_builtin_callback(self):
        with patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=FakeYOLO)}):
            first, second = PersonTracker(), PersonTracker()
        # Real Ultralytics callbacks and ByteTrack, without weights or inference.
        from ultralytics.trackers.track import on_predict_start
        predictors = [SimpleNamespace(args=SimpleNamespace(task='detect')) for _ in range(2)]
        for tracker, predictor in zip((first, second), predictors):
            tracker._prepare_tracker(predictor)
            installed = predictor.trackers[0]
            on_predict_start(predictor, persist=True)
            self.assertIs(predictor.trackers[0], installed)
        self.assertIsNot(predictors[0].trackers[0], predictors[1].trackers[0])

    def test_expired_ids_and_long_gap_do_not_reuse_observations_or_reload_model(self):
        with patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=FakeYOLO)}):
            tracker = PersonTracker()
        frame = np.zeros((48, 64, 3), np.uint8)
        with patch('detection.person_tracker.time.monotonic', side_effect=[10, 10, 11, 11, 15, 15]):
            first = tracker.track(frame)
            self.assertEqual([p['person_id'] for p in tracker.track(frame)], [1, 2])
            returned = tracker.track(frame)
        self.assertEqual([p['person_id'] for p in first], [1, 2])
        self.assertEqual([p['person_id'] for p in returned], [3, 4])
        self.assertEqual(tracker.tracking_generation, 1)
        tracker.model.predictor.trackers[0].reset.assert_called_once()
        self.assertEqual(tracker.model.calls, 3)

    def test_raw_id_switch_does_not_assign_one_stable_id_to_two_people(self):
        mapper = StablePersonIdMapper()
        box = (0, 0, 20, 40)
        first = mapper.resolve(1, box)
        mapper.finish_frame()
        self.assertEqual(mapper.resolve(2, box), first)
        mapper.finish_frame()
        self.assertEqual(mapper.resolve(2, box), first)
        self.assertNotEqual(mapper.resolve(1, box), first)

    def test_no_frame_expiry_finishes_clip_before_returning_detection(self):
        with TemporaryDirectory() as temp, patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=FakeYOLO)}):
            worker = VideoWorker(source='rtsp://example.test/live', ai_cctv_path=temp)
            worker.stream = MagicMock(is_rtsp=True)
            worker.stream.open.return_value = True
            worker.stream.get_fps.return_value = 15
            worker.stream.pop_connection_events.return_value = []
            worker.stream.release.return_value = True
            worker.stream.receiver.is_connected = False
            frames, events = [], []
            worker.frame_ready.connect(frames.append)
            worker.event_ready.connect(events.append)
            step = 0

            def read():
                nonlocal step
                step += 1
                if step == 1:
                    return True, np.zeros((48, 64, 3), np.uint8)
                if step == 2:
                    for state in worker.state_manager.person_states.values():
                        state['last_seen'] -= 4
                    return False, None
                if step == 3:
                    self.assertFalse(worker.state_manager.person_states)
                    self.assertFalse(worker.clip_manager.person_clips)
                    self.assertEqual(len(list(worker.run_directory.rglob('trajectory.jpg'))), 2)
                    return True, np.zeros((48, 64, 3), np.uint8)
                worker.request_stop()
                return False, None

            worker.stream.read.side_effect = read
            worker.run()
            self.assertTrue(worker.cleanup_ok)
            self.assertEqual(len(frames), 2)
            self.assertEqual(len([e for e in events if e['type'] == 'disappear']), 2)
            self.assertEqual(len(list(worker.run_directory.rglob('preview.mp4'))), 4)
            self.assertIsNone(worker.tracker)

    def test_clip_deadline_keeps_collecting_trajectory_and_unannotated_full_frame(self):
        with TemporaryDirectory() as temp:
            manager = ClipManager(temp, fps=10, max_clip_seconds=1)
            frame = np.full((48, 64, 3), 70, np.uint8)
            started = datetime(2026, 10, 8)
            with patch('storage.clip_manager.datetime') as clock:
                clock.now.return_value = started
                manager.update_person(1, frame, (0, 0, 10, 30))
                state = manager.person_clips[1]
                clock.now.return_value = started + timedelta(seconds=2)
                manager.update_person(1, frame, (0, 0, 10, 30))
            self.assertTrue(state['clip_completed'])
            self.assertIsNone(state['writer'])
            for _ in range(8):
                manager.update_person(1, frame, (30, 0, 40, 30))
            self.assertGreater(len(state['points']), 1)
            folder = Path(state['folder_path'])
            manager.finish_all()
            self.assertTrue((folder / 'trajectory.jpg').is_file())
            self.assertFalse((folder / 'full_crop.jpg').exists())
            capture = cv2.VideoCapture(str(folder / 'preview.mp4'))
            try:
                ok, saved = capture.read()
                self.assertTrue(ok)
                self.assertEqual(saved.shape, frame.shape)
                self.assertLess(float(saved.std()), 3)
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT), 11)
            finally:
                capture.release()

    def test_writer_open_and_trajectory_encoding_failures_are_not_success(self):
        frame = np.zeros((48, 64, 3), np.uint8)
        with TemporaryDirectory() as temp:
            recording, clips = RecordingManager(temp), ClipManager(temp)
            with patch('cv2.VideoWriter') as factory:
                factory.return_value.isOpened.return_value = False
                with self.assertRaises(OSError):
                    recording.write_frame(frame)
                with self.assertRaises(OSError):
                    clips.update_person(1, frame, (0, 0, 20, 40))
                self.assertEqual(factory.return_value.release.call_count, 2)
            with patch('cv2.imencode', return_value=(False, None)):
                with self.assertRaises(OSError):
                    clips.finish_all()
            self.assertIn(1, clips.person_clips)

    def test_one_finish_failure_does_not_skip_other_person_writers(self):
        with TemporaryDirectory() as temp:
            clips = ClipManager(temp)
            for person_id in (1, 2):
                clips.update_person(person_id, np.zeros((48, 64, 3), np.uint8), (0, 0, 20, 40))
            with patch.object(clips, '_save_trajectory_image', side_effect=[OSError('disk'), None]):
                with self.assertRaises(OSError):
                    clips.finish_all()
            self.assertIsNone(clips.person_clips[1]['writer'])
            self.assertNotIn(2, clips.person_clips)

    def test_recording_rename_failure_and_same_second_segments(self):
        with TemporaryDirectory() as temp:
            manager = RecordingManager(temp)
            frame = np.zeros((48, 64, 3), np.uint8)
            for _ in range(2):
                manager.write_frame(frame)
                manager.stop_recording()
            self.assertEqual(len(list(Path(temp).rglob('*.mp4'))), 2)
            manager.write_frame(frame)
            with patch('storage.recording_manager.os.rename', side_effect=PermissionError):
                with self.assertRaises(OSError):
                    manager.stop_recording()
            self.assertTrue(Path(manager.temp_save_path).exists())

    def test_recovery_zip_and_merged_destination_stay_in_camera_run(self):
        import io
        import zipfile
        with TemporaryDirectory() as temp:
            for camera_id in (7, 8):
                root = Path(temp) / 'cameras' / f'camera_{camera_id}' / 'execution'
                manager = NetworkRecoveryManager(camera_id=f'camera_{camera_id}', base_dir=str(root))
                response = MagicMock(headers={})
                zipped = io.BytesIO()
                with zipfile.ZipFile(zipped, 'w') as archive:
                    archive.writestr('chunk001.ts', b'synthetic')
                response.iter_content.return_value = [zipped.getvalue()]
                payload = manager.build_payload(datetime(2026, 10, 8), datetime(2026, 10, 8, 0, 0, 4))
                saved = manager._save_file_response(response, payload)
                self.assertTrue(Path(saved).is_relative_to(root / '복구 영상'))
                with patch.object(manager, '_merge_ts_files', return_value={'success': True}) as merge:
                    result = manager._extract_and_merge(saved, payload)
                self.assertTrue(Path(result['file_path']).is_relative_to(root / '원본 녹화본'))
                self.assertEqual(len(merge.call_args.args[0]), 1)

    def test_recovery_thread_exception_is_reported_and_cleanup_waits(self):
        with patch('workers.video_worker.CropManager'):
            worker = VideoWorker(use_yolo=False)
        worker.stream = MagicMock()
        worker.recovery_manager = MagicMock()
        worker.recovery_manager.record_recovery.side_effect = PermissionError('secret path')
        events = []
        worker.event_ready.connect(events.append)
        worker._run_recovery_request({})
        self.assertEqual(events[-1]['type'], 'error')
        self.assertIn('저장 실패', events[-1]['message'])
        self.assertNotIn('secret', events[-1]['message'])
        worker._cleanup()
        self.assertTrue(worker.cleanup_ok)

    def test_worker_storage_failure_reports_error_and_releases_stream(self):
        with TemporaryDirectory() as temp:
            worker = VideoWorker(use_yolo=False, ai_cctv_path=temp)
            worker.stream = MagicMock(is_rtsp=False)
            worker.stream.get_fps.return_value = 15
            worker.stream.pop_connection_events.return_value = []
            worker.stream.read.return_value = (True, np.zeros((48, 64, 3), np.uint8))
            events = []
            worker.event_ready.connect(events.append)
            with patch('cv2.VideoWriter') as factory:
                factory.return_value.isOpened.return_value = False
                worker.run()
            self.assertIn('저장 실패', events[-1]['message'])
            self.assertTrue(worker.storage_errors)
            self.assertTrue(worker.cleanup_ok)
            worker.stream.release.assert_called_once()

    def test_worker_trajectory_failure_blocks_cleanup_success_but_releases_stream(self):
        with TemporaryDirectory() as temp:
            worker = VideoWorker(ai_cctv_path=temp)
            worker.stream = MagicMock()
            worker.clip_manager = ClipManager(worker.ai_cctv_path)
            worker.clip_manager.update_person(1, np.zeros((48, 64, 3), np.uint8), (0, 0, 20, 40))
            with patch('cv2.imencode', return_value=(False, None)):
                worker._cleanup()
            self.assertFalse(worker.cleanup_ok)
            worker.stream.release.assert_called_once()

    def test_no_storage_path_does_not_enable_recording_clips_or_recovery(self):
        with patch('workers.video_worker.CropManager'):
            worker = VideoWorker(use_yolo=False, source='rtsp://example.test/live', edge_api_base_url='http://example.test')
        worker.stream = MagicMock(is_rtsp=True)
        worker.stream.pop_connection_events.return_value = []
        worker.stream.read.side_effect = lambda: (worker.request_stop() or False, None)
        worker.run()
        self.assertIsNone(worker.recording_manager)
        self.assertIsNone(worker.clip_manager)
        self.assertIsNone(worker.recovery_manager)


if __name__ == '__main__':
    unittest.main()
