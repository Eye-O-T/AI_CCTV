"""확정 글로벌 ID별 완료 파일 이동·폴더명 확정·부분 실패 보존 검증."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4
import errno
import os
import time
import unittest

import cv2
import numpy as np
from test_camera_runtime import APP
from reid.tasks import Observation
from storage.clip_manager import ClipManager
from workers.crop_worker import CropWorker
from workers.video_worker import VideoWorker


class PersonMediaPublishTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / '사용자' / 'AI_CCTV'
        self.frame = np.random.default_rng(7).integers(0, 256, (240, 320, 3), dtype=np.uint8)
        self.box = (20, 20, 150, 190)
        self.updates, self.errors = [], []
        self.manager = self.make_manager()
        self.source = self.root / 'output' / 'crop_01.jpg'
        self.source.parent.mkdir()
        ok, image = cv2.imencode('.jpg', self.frame)
        self.assertTrue(ok)
        self.source.write_bytes(image.tobytes())

    def make_manager(self, camera_id=7):
        manager = ClipManager(self.root / 'temp', fps=10, max_clip_seconds=1,
                              camera_id=camera_id, execution_id=uuid4().hex,
                              destination_root=self.destination, on_update=self.updates.append,
                              on_error=self.errors.append)
        self.addCleanup(manager.finish_all)
        return manager

    def prepare(self, manager=None):
        manager = manager or self.manager
        manager.update_person(1, self.frame, self.box)
        task_id = uuid4()
        manager.save_representative_crop(1, self.source, 0, task_id)
        return Observation(task_id, manager.camera_id, manager.execution_id, 0, 1,
                           datetime.now(timezone.utc))

    def test_crop_moves_at_assignment_clip_after_close_and_folder_after_end(self):
        started = datetime(2026, 11, 19, 14, 3, 12)
        original = self.source.read_bytes()
        with patch('storage.clip_manager.datetime') as clock:
            clock.now.return_value = started
            observation = self.prepare()
            state = self.manager.person_clips[1]
            staging = Path(state['folder_path'])
            self.assertFalse(list(self.destination.rglob('crop.jpg')))
            self.assertTrue(self.manager.assign_global_person(observation, 12))
            live = self.destination / '이벤트/person_12/2026-11-19/camera_7/14시03분12초'
            self.assertEqual((live / 'crop.jpg').read_bytes(), original)
            self.assertFalse((live / 'clip.mp4').exists())
            self.assertIsNotNone(state['writer'])
            clock.now.return_value += timedelta(seconds=2)
            self.manager.update_person(1, self.frame, self.box)
            self.assertIsNone(state['writer'])
            self.assertTrue((live / 'clip.mp4').is_file())
            self.assertFalse((staging / 'clip.mp4').exists())
            clock.now.return_value += timedelta(seconds=2)
            self.manager.update_person(1, self.frame, self.box)
            self.manager.finish_all()
        final = live.with_name('14시03분12초~14시03분16초')
        self.assertFalse(live.exists())
        self.assertFalse(staging.exists())
        self.assertFalse(Path(self.manager.clip_root_dir).exists())
        self.assertEqual({p.name for p in final.iterdir()}, {'clip.mp4', 'crop.jpg', 'trajectory.jpg'})
        self.assertEqual(self.updates[-1].folder_path, final)
        self.assertEqual(self.updates[-1].global_person_id, 12)
        self.assertFalse(self.updates[-1].move_failed)
        self.assertEqual(self.source.read_bytes(), original)

    def test_clip_ready_before_matching_and_duplicate_result_does_not_duplicate_files(self):
        observation = self.prepare()
        self.manager._close_writer(self.manager.person_clips[1])
        self.assertFalse(list(self.destination.rglob('*.mp4')))
        self.assertTrue(self.manager.assign_global_person(observation, 12))
        self.assertTrue(self.manager.assign_global_person(observation, 12))
        self.assertFalse(self.manager.assign_global_person(observation, 13))
        self.assertEqual(len(list(self.destination.rglob('clip.mp4'))), 1)
        self.assertEqual(len(list(self.destination.rglob('crop.jpg'))), 1)
        self.manager.finish_all()

    def test_wrong_camera_execution_generation_person_task_and_late_result_are_rejected(self):
        observation = self.prepare()
        for changes in ({'camera_id': 8}, {'execution_id': uuid4().hex}, {'tracking_generation': 1},
                        {'local_person_id': 2}, {'task_id': uuid4()}):
            with self.subTest(changes=changes):
                self.assertFalse(self.manager.assign_global_person(replace(observation, **changes), 12))
        self.assertFalse(self.manager.assign_global_person(observation, True))
        self.manager.finish_all()
        self.assertFalse(self.manager.assign_global_person(observation, 12))
        self.assertFalse(list(self.destination.rglob('crop.jpg')))
        self.assertTrue(self.updates[-1].crop_path.exists())

    def test_colliding_start_and_final_directories_never_merge_people(self):
        with patch('storage.clip_manager.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 19, 14, 0, 0)
            second, other_camera = self.make_manager(), self.make_manager(8)
            observations = [(manager, self.prepare(manager)) for manager in (self.manager, second, other_camera)]
            existing = self.destination / '이벤트/person_12/2026-11-19/camera_7/14시00분00초~14시00분00초'
            existing.mkdir(parents=True)
            (existing / 'crop.jpg').write_bytes(b'preserve-existing')
            for manager, observation in observations:
                self.assertTrue(manager.assign_global_person(observation, 12))
                manager.finish_all()
        self.assertEqual((existing / 'crop.jpg').read_bytes(), b'preserve-existing')
        self.assertTrue(existing.with_name(existing.name + '_2').is_dir())
        self.assertTrue(existing.with_name(existing.name + '_3').is_dir())
        self.assertEqual(len(list(self.destination.glob('이벤트/person_12/*/camera_8/*/clip.mp4'))), 1)

    def test_midnight_final_name_uses_start_date_and_end_date(self):
        with patch('storage.clip_manager.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 19, 23, 59, 59)
            observation = self.prepare()
            self.manager.assign_global_person(observation, 12)
            clock.now.return_value += timedelta(seconds=2)
            self.manager.update_person(1, self.frame, self.box)
            self.manager.finish_all()
        final = self.destination / '이벤트/person_12/2026-11-19/camera_7/23시59분59초~2026-11-20_00시00분01초'
        self.assertTrue((final / 'trajectory.jpg').is_file())

    def test_partial_move_failure_preserves_source_without_automatic_retry(self):
        observation = self.prepare()
        self.manager._close_writer(self.manager.person_clips[1])
        real_rename = os.rename
        attempts = []

        def move(source, destination):
            if Path(source).name == 'clip.mp4':
                attempts.append(source)
                raise PermissionError
            real_rename(source, destination)

        with patch('storage.clip_manager.os.rename', side_effect=move):
            self.manager.assign_global_person(observation, 12)
            self.manager.finish_all()
        final = self.updates[-1]
        self.assertEqual(len(attempts), 1)
        self.assertTrue(final.move_failed)
        self.assertTrue(final.clip_path.is_relative_to(self.root / 'temp'))
        self.assertTrue(final.clip_path.exists())
        self.assertTrue(final.crop_path.is_relative_to(self.destination))
        self.assertTrue(final.trajectory_path.is_relative_to(self.destination))
        self.assertTrue(final.crop_path.exists())
        self.assertTrue(final.trajectory_path.exists())
        self.assertEqual(len(self.errors), 1)
        self.assertTrue(self.manager.storage_failed)

    def test_final_directory_rename_failure_keeps_completed_files_in_start_folder(self):
        observation = self.prepare()
        self.manager.assign_global_person(observation, 12)
        real_rename = os.rename

        def move(source, destination):
            if Path(source).is_dir():
                raise PermissionError
            real_rename(source, destination)

        with patch('storage.clip_manager.os.rename', side_effect=move):
            self.manager.finish_all()
        final = self.updates[-1]
        self.assertNotIn('~', final.folder_path.name)
        self.assertTrue(final.move_failed)
        for path in (final.clip_path, final.crop_path, final.trajectory_path):
            self.assertTrue(path.is_file())

    def test_directory_creation_failure_keeps_all_temp_sources(self):
        observation = self.prepare()
        with patch('storage.clip_manager.Path.mkdir', side_effect=PermissionError):
            self.manager.assign_global_person(observation, 12)
        self.manager.finish_all()
        final = self.updates[-1]
        self.assertTrue(final.move_failed)
        for path in (final.clip_path, final.crop_path, final.trajectory_path):
            self.assertTrue(path.is_relative_to(self.root / 'temp'))
            self.assertTrue(path.is_file())

    def test_existing_file_is_preserved_when_it_appears_before_clip_completion(self):
        observation = self.prepare()
        self.manager.assign_global_person(observation, 12)
        directory = self.updates[-1].folder_path
        (directory / 'clip.mp4').write_bytes(b'preserve-existing')
        self.manager.finish_all()
        final = self.updates[-1]
        self.assertTrue(final.move_failed)
        self.assertEqual((final.folder_path / 'clip.mp4').read_bytes(), b'preserve-existing')
        self.assertTrue(final.clip_path.is_relative_to(self.root / 'temp'))

    def test_other_volume_failure_never_falls_back_to_copy(self):
        observation = self.prepare()
        with patch('storage.clip_manager.os.rename', side_effect=OSError(errno.EXDEV, 'volume')):
            self.manager.assign_global_person(observation, 12)
        self.assertTrue(self.updates[-1].crop_path.is_relative_to(self.root / 'temp'))
        self.assertFalse(list(self.destination.rglob('crop.jpg')))

    def test_worker_cleanup_reports_publish_failure_and_releases_stream(self):
        observation = self.prepare()
        with patch('storage.clip_manager.os.rename', side_effect=PermissionError):
            self.manager.assign_global_person(observation, 12)
        worker = VideoWorker(camera_id=7)
        worker.clip_manager = self.manager
        worker.stream = MagicMock()
        worker._cleanup()
        self.assertFalse(worker.cleanup_ok)
        worker.stream.release.assert_called_once()
        self.assertTrue(self.source.is_file())
        worker.deleteLater()

    def test_representative_copy_failure_still_allows_matching_same_observation(self):
        self.manager.update_person(1, self.frame, self.box)
        task_id = uuid4()
        with patch('storage.clip_manager.shutil.copyfile', side_effect=PermissionError):
            with self.assertRaises(OSError):
                self.manager.save_representative_crop(1, self.source, 0, task_id)
        observation = Observation(task_id, 7, self.manager.execution_id, 0, 1, datetime.now(timezone.utc))
        self.assertTrue(self.manager.assign_global_person(observation, 12))
        self.manager.finish_all()
        final = self.updates[-1]
        self.assertIsNone(final.crop_path)
        self.assertTrue(final.clip_path.is_relative_to(self.destination))
        self.assertTrue(final.trajectory_path.is_relative_to(self.destination))
        self.assertTrue(self.source.is_file())

    def test_real_crop_reid_threads_publish_all_media_on_stop(self):
        from test_reid_pipeline import Api, Extractor
        from reid.pipeline import ReIDPipeline
        api, extractor = Api(), Extractor()
        pipeline = ReIDPipeline('http://test', 'token', lambda: extractor, lambda *args: api)
        worker = VideoWorker(camera_id=7, ai_cctv_path=str(self.destination),
                             clip_max_seconds=.05, reid_pipeline=pipeline)
        worker.crop_worker = CropWorker(7, output_root=self.root / 'live-output', sample_interval=0)
        worker.stream = MagicMock(is_rtsp=False)
        worker.stream.get_fps.return_value = 10
        worker.stream.pop_connection_events.return_value = []
        updates = []
        worker.media_ready.connect(updates.append)
        deadline = time.monotonic() + 5
        count = 0

        def read():
            nonlocal count
            count += 1
            time.sleep(.01)
            if (worker.global_person_ids and count > 20) or time.monotonic() > deadline:
                worker.request_stop()
                return False, None
            return True, self.frame.copy()

        worker.stream.read.side_effect = read
        tracker = SimpleNamespace(tracking_generation=0, track=lambda frame: [
            dict(person_id=1, bbox=self.box, conf=.9)], expire_persons=lambda ids: None)
        try:
            with patch('workers.video_worker.PersonTracker', return_value=tracker), \
                    patch('workers.video_worker.DEFAULT_MEDIA_TEMP_ROOT', self.root / 'live-temp'), \
                    patch('storage.recording_manager.DEFAULT_TEMP_ROOT', self.root / 'live-temp'):
                worker.run()
            self.assertTrue(worker.cleanup_ok)
            final = updates[-1]
            self.assertEqual(final.global_person_id, 1)
            self.assertIn('~', final.folder_path.name)
            self.assertTrue(final.folder_path.is_relative_to(self.destination / '이벤트/person_1'))
            for path in (final.clip_path, final.crop_path, final.trajectory_path):
                self.assertTrue(path.is_file())
                self.assertEqual(path.parent, final.folder_path)
            capture = cv2.VideoCapture(str(final.clip_path))
            try:
                self.assertTrue(capture.read()[0])
            finally:
                capture.release()
            self.assertIsNotNone(cv2.imdecode(np.fromfile(final.trajectory_path, np.uint8), cv2.IMREAD_COLOR))
            self.assertEqual(len(list((self.root / 'live-output').rglob('crop_*.jpg'))), 5)
            self.assertFalse(list((self.root / 'live-temp').rglob('clip.mp4')))
        finally:
            worker.request_stop()
            pipeline.request_stop()
            pipeline.join(5)
            self.assertFalse(pipeline.is_alive())
            worker.deleteLater()
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
