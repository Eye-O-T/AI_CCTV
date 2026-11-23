"""인물별 temp 저장·독립 완료 알림·늦은 crop 격리. 실제 카메라/AI 모델은 사용하지 않는다."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4
import time
import unittest

import cv2
import numpy as np
from test_camera_runtime import APP
from storage.clip_manager import ClipManager
from workers.crop_worker import CropWorker
from workers.video_worker import VideoWorker


class PersonMediaTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.frame = np.random.default_rng(7).integers(0, 256, (240, 320, 3), dtype=np.uint8)
        self.box = (20, 20, 150, 190)
        self.updates = []
        self.manager = ClipManager(self.root / 'temp', fps=10, max_clip_seconds=1,
                                   camera_id=7, execution_id=uuid4().hex, on_update=self.updates.append)
        self.addCleanup(self.manager.finish_all)
        self.paths = []
        for index in range(1, 6):
            path = self.root / 'output' / f'crop_{index:02}.jpg'
            path.parent.mkdir(exist_ok=True)
            ok, image = cv2.imencode('.jpg', self.frame)
            self.assertTrue(ok)
            path.write_bytes(image.tobytes())
            self.paths.append(path)

    def test_clip_crop_trajectory_complete_separately_and_keep_osnet_sources(self):
        started = datetime(2026, 11, 19, 14, 0, 0)
        originals = [p.read_bytes() for p in self.paths]
        with patch('storage.clip_manager.datetime') as clock:
            clock.now.return_value = started
            self.manager.update_person(1, self.frame, self.box)
            self.assertFalse(self.updates)
            clock.now.return_value = started + timedelta(seconds=2)
            self.manager.update_person(1, self.frame, self.box)
            clip = self.updates[-1]
            self.assertTrue(clip.clip_path.is_file())
            self.assertIsNone(clip.crop_path)
            self.assertIsNone(clip.trajectory_path)
            self.assertIsNone(clip.finished_at)
            self.assertIsNone(self.manager.person_clips[1]['writer'])
            task_id = uuid4()
            self.assertTrue(self.manager.save_representative_crop(1, self.paths[0], 0, task_id))
            crop = self.updates[-1]
            self.assertEqual(crop.crop_path.read_bytes(), originals[0])
            self.assertEqual(crop.crop_task_id, task_id)
            self.assertIsNone(crop.trajectory_path)
            # 클립이 끝난 뒤에도 추적 점과 마지막 관측 시각은 계속 갱신한다.
            clock.now.return_value += timedelta(seconds=1)
            for _ in range(10):
                self.manager.update_person(1, self.frame, (60, 20, 190, 190))
            self.manager.finish_person(1)
        final = self.updates[-1]
        self.assertEqual(final.finished_at, started + timedelta(seconds=3))
        self.assertIsNotNone(final.trajectory_path)
        self.assertTrue(cv2.imdecode(np.fromfile(final.trajectory_path, np.uint8), cv2.IMREAD_COLOR) is not None)
        self.assertFalse(self.manager.person_clips)
        self.assertEqual([p.read_bytes() for p in self.paths], originals)
        self.assertEqual((final.camera_id, final.execution_id, final.tracking_generation, final.local_person_id),
                         (7, self.manager.execution_id, 0, 1))
        capture = cv2.VideoCapture(str(final.clip_path))
        try:
            self.assertTrue(capture.read()[0])
        finally:
            capture.release()

    def test_crop_can_complete_before_full_length_clip_and_stop(self):
        self.manager.max_clip_seconds = None
        self.manager.update_person(1, self.frame, self.box)
        self.manager.save_representative_crop(1, self.paths[0], 0, uuid4())
        self.assertIsNone(self.updates[-1].clip_path)
        self.assertIsNotNone(self.updates[-1].crop_path)
        self.manager.finish_all()
        self.assertIsNotNone(self.updates[-1].clip_path)
        self.assertIsNotNone(self.updates[-1].trajectory_path)

    def test_reused_id_generation_camera_and_same_time_runs_are_isolated(self):
        with patch('storage.clip_manager.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 19, 14, 0, 0)
            other = ClipManager(self.root / 'temp', camera_id=7)
            restarted = ClipManager(self.root / 'temp', camera_id=7)
            camera = ClipManager(self.root / 'temp', camera_id=8)
            self.addCleanup(other.finish_all)
            self.addCleanup(restarted.finish_all)
            self.addCleanup(camera.finish_all)
            self.manager.update_person(1, self.frame, self.box)
            first = self.manager.person_clips[1]['folder_path']
            self.manager.update_person(1, self.frame, self.box, generation=1)
            self.assertNotEqual(first, self.manager.person_clips[1]['folder_path'])
            self.assertFalse(self.manager.save_representative_crop(1, self.paths[0], 0, uuid4()))
            other.update_person(1, self.frame, self.box)
            camera.update_person(1, self.frame, self.box)
            self.assertEqual(len({self.manager.clip_root_dir, other.clip_root_dir,
                                  restarted.clip_root_dir, camera.clip_root_dir}), 4)
            self.assertNotIn(self.manager.execution_id, self.manager.clip_root_dir)

    def test_copy_failure_is_not_ready_and_does_not_modify_source(self):
        self.manager.update_person(1, self.frame, self.box)
        original = self.paths[0].read_bytes()
        for operation in ('shutil.copyfile', 'os.rename'):
            with self.subTest(operation=operation), patch('storage.clip_manager.' + operation, side_effect=PermissionError):
                with self.assertRaises(OSError):
                    self.manager.save_representative_crop(1, self.paths[0], 0, uuid4())
        self.assertFalse(self.manager.person_clips[1]['crop_saved'])
        self.assertFalse(self.updates)
        self.assertEqual(self.paths[0].read_bytes(), original)

    def test_end_without_five_crops_keeps_clip_and_trajectory_without_crop_ready(self):
        self.manager.update_person(1, self.frame, self.box)
        self.manager.finish_all()
        final = self.updates[-1]
        self.assertIsNotNone(final.clip_path)
        self.assertIsNotNone(final.trajectory_path)
        self.assertIsNone(final.crop_path)
        self.assertFalse((final.folder_path / 'crop.jpg').exists())

    def test_writer_and_trajectory_failure_never_report_unfinished_file_as_ready(self):
        with patch('storage.clip_manager.cv2.VideoWriter') as factory:
            factory.return_value.isOpened.return_value = False
            with self.assertRaises(OSError):
                self.manager.update_person(1, self.frame, self.box)
        self.assertFalse(self.manager.person_clips[1]['clip_completed'])
        self.assertFalse(self.updates)
        self.manager.update_person(1, self.frame, self.box)
        with patch('storage.clip_manager.cv2.imencode', return_value=(False, None)):
            with self.assertRaises(OSError):
                self.manager.finish_all()
        self.assertIsNotNone(self.updates[-1].clip_path)
        self.assertIsNone(self.updates[-1].trajectory_path)
        self.assertIsNone(self.updates[-1].finished_at)

    def worker_with_crop_result(self):
        worker = VideoWorker(camera_id=7, ai_cctv_path=str(self.root / 'user'))
        worker.clip_manager = self.manager
        worker.crop_worker = MagicMock()
        result = dict(person_id=1, generation=0, task_id=uuid4(),
                      observed_at=datetime.now(timezone.utc), paths=tuple(self.paths), error=None)
        worker.crop_worker.take_results.return_value = [result]
        worker.crop_worker.is_result_current.return_value = True
        self.manager.update_person(1, self.frame, self.box)
        self.addCleanup(worker.deleteLater)
        return worker, result

    def test_stale_crop_is_ignored_and_representative_failure_keeps_reid_input(self):
        worker, result = self.worker_with_crop_result()
        worker.crop_worker.is_result_current.return_value = False
        worker._poll_crop_results()
        self.assertFalse(self.manager.person_clips[1]['crop_saved'])
        worker.crop_worker.is_result_current.return_value = True
        worker.osnet_worker = object()
        errors = []
        worker.event_ready.connect(errors.append)
        with patch('storage.clip_manager.shutil.copyfile', side_effect=PermissionError):
            worker._poll_crop_results()
        self.assertEqual(list(worker._reid_pending), [result])
        self.assertTrue(worker.running)
        self.assertIn('대표 crop', errors[-1]['message'])

    def test_tracking_reset_finishes_previous_media_before_reusing_local_id(self):
        worker, _ = self.worker_with_crop_result()
        worker.tracker = SimpleNamespace(tracking_generation=1)
        worker._collect_crops(self.frame, [])
        self.assertFalse(self.manager.person_clips)
        self.assertIsNotNone(self.updates[-1].finished_at)
        self.assertEqual(self.updates[-1].tracking_generation, 0)

    def test_real_crop_thread_and_stop_create_all_media_only_in_temp(self):
        destination = self.root / 'user' / 'AI_CCTV'
        worker = VideoWorker(camera_id=7, ai_cctv_path=str(destination), clip_max_seconds=.05)
        worker.crop_worker = CropWorker(7, output_root=self.root / 'live-output', sample_interval=0)
        worker.stream = MagicMock(is_rtsp=False)
        worker.stream.get_fps.return_value = 10
        worker.stream.pop_connection_events.return_value = []
        updates = []
        worker.media_ready.connect(updates.append)
        count = 0

        def read():
            nonlocal count
            count += 1
            time.sleep(.01)
            if count > 40:
                worker.request_stop()
                return False, None
            return True, self.frame.copy()

        worker.stream.read.side_effect = read
        tracker = SimpleNamespace(tracking_generation=0, track=lambda frame: [
            dict(person_id=1, bbox=self.box, conf=.9)], expire_persons=lambda ids: None)
        with patch('workers.video_worker.PersonTracker', return_value=tracker), \
                patch('workers.video_worker.DEFAULT_MEDIA_TEMP_ROOT', self.root / 'live-temp'), \
                patch('storage.recording_manager.DEFAULT_TEMP_ROOT', self.root / 'live-temp'):
            worker.run()
        self.assertTrue(worker.cleanup_ok)
        self.assertEqual(len(list((self.root / 'live-output').rglob('crop_*.jpg'))), 5)
        final = updates[-1]
        for path in (final.clip_path, final.crop_path, final.trajectory_path):
            self.assertTrue(path.is_file())
            self.assertTrue(path.is_relative_to(self.root / 'live-temp'))
        self.assertIsNotNone(final.finished_at)
        self.assertFalse(list(destination.rglob('clip.mp4')))
        self.assertFalse(list(destination.rglob('trajectory.jpg')))
        self.assertFalse(list(destination.rglob('crop.jpg')))
        self.assertFalse((destination / 'cameras').exists())
        self.assertTrue(list(destination.rglob('*.mp4')))  # 일반 원본은 1단계 경로로 이동한다.
        worker.deleteLater()
        APP.processEvents()


if __name__ == '__main__':
    unittest.main()
