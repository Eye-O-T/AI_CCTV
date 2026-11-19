"""합성 입력으로 비동기 전신 crop 저장과 영상 처리 분리를 검증한다."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
from types import SimpleNamespace
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import test_camera_runtime  # offscreen Qt 및 client_code import 경로
from workers.crop_worker import CropWorker
from workers.video_worker import VideoWorker


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('CropWorker condition timed out')
        time.sleep(.005)


class CropWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = CropWorker(1, output_root=self.temp.name)
        self.addCleanup(self.stop_worker)
        self.frame = np.random.default_rng(10).integers(0, 256, (240, 320, 3), dtype=np.uint8)
        self.bbox = (20, 20, 150, 190)
        self.clock = 0.0
        clock = patch('workers.crop_worker.time', SimpleNamespace(monotonic=lambda: self.clock))
        clock.start()
        self.addCleanup(clock.stop)

    def stop_worker(self):
        self.worker.request_stop()
        if self.worker.ident is not None:
            self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())

    def submit(self, at, person_id=1, bbox=None, frame=None):
        self.clock = at
        return self.worker.submit(person_id, self.frame if frame is None else frame,
                                  self.bbox if bbox is None else bbox)

    def test_interval_and_full_queue_skip_without_copying_more_candidates(self):
        self.worker.tasks.maxsize = 1
        self.assertTrue(self.submit(0))
        expected = self.frame[20:190, 20:150].copy()
        self.frame[:] = 0
        self.assertFalse(self.submit(.49))
        self.assertFalse(self.submit(.5))
        self.assertEqual(self.worker.tasks.qsize(), 1)
        np.testing.assert_array_equal(self.worker.tasks.get_nowait().image, expected)
        self.worker.request_stop()
        self.assertFalse(self.submit(1))

    def test_full_body_size_and_sharpness_filter_then_save_only_once(self):
        self.worker.start()
        # 기존 FullBodyChecker의 상단 여백 조건에 걸리는 후보.
        self.submit(0, bbox=(20, 0, 150, 190))
        # 전신 조건은 통과하지만 크기가 작은 후보와 흐린 후보.
        self.submit(.5, bbox=(20, 20, 60, 190))
        self.submit(1, frame=np.zeros_like(self.frame))
        for at in (1.5, 2, 2.5, 3):
            self.submit(at)
        processed = Event()
        original = self.worker.collector._save

        def save(*args):
            paths = original(*args)
            processed.set()
            return paths

        with patch.object(self.worker.collector, '_save', side_effect=save):
            self.submit(3.5)
            self.assertTrue(processed.wait(3))
            wait_until(lambda: self.worker._observations[1].finished)
        result = self.worker.take_results()
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]['error'])
        self.assertEqual(len(result[0]['paths']), 5)
        for path in result[0]['paths']:
            decoded = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), 1)
            self.assertEqual(decoded.shape, (170, 130, 3))
        self.assertFalse(self.submit(4))
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.jpg'))), 5)

    def test_capture_time_and_generation_are_preserved_and_reset_discards_pending(self):
        self.submit(0)
        first = self.worker.tasks.get_nowait()
        self.assertEqual(first.generation, 0)
        self.assertEqual(first.frame_shape, self.frame.shape)
        self.assertEqual(first.bbox, self.bbox)
        self.assertIsNotNone(first.captured_at.utcoffset())
        self.assertEqual(first.captured_at.astimezone().replace(tzinfo=None), first.first_seen)
        self.submit(.5)
        self.worker.reset(1)
        self.assertTrue(self.worker.tasks.empty())
        self.submit(1)
        second = self.worker.tasks.get_nowait()
        self.assertEqual(second.generation, 1)

    def test_save_failure_is_reported_once_and_does_not_stop_consumer(self):
        with patch('storage.crop_collector.cv2.imencode', return_value=(False, None)):
            for at in (0, .5, 1, 1.5, 2):
                self.submit(at)
            self.worker.start()
            wait_until(lambda: self.worker._observations[1].finished)
            results = self.worker.take_results()
            self.assertEqual(len(results), 1)
            self.assertIn('실패', results[0]['error'])
            self.assertFalse(results[0]['paths'])
            self.assertTrue(self.worker.is_alive())
            self.assertFalse(self.submit(3))
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    def test_video_keeps_emitting_frames_during_blocked_crop_save(self):
        video = VideoWorker(use_yolo=True, use_vlm=False)
        video.crop_worker = self.worker
        self.worker.sample_interval = 0  # 0.5초 수집 간격 자체는 별도 테스트에서 확인.
        video.stream = MagicMock(is_rtsp=False)
        video.stream.open.return_value = True
        video.stream.pop_connection_events.return_value = []
        video.stream.release.return_value = True
        person = dict(person_id=1, bbox=self.bbox, conf=.9)
        tracker = SimpleNamespace(tracking_generation=0, track=lambda frame: [person])
        save_started, release_save, saved = Event(), Event(), Event()
        self.addCleanup(release_save.set)
        processed = [0]
        save_thread_ids = []
        original_save = self.worker.collector._save
        original_collect = self.worker.collector.collect_image

        def slow_save(*args):
            save_thread_ids.append(get_ident())
            save_started.set()
            if not release_save.wait(3):
                raise OSError('test save release timed out')
            paths = original_save(*args)
            saved.set()
            return paths

        def collect(*args):
            result = original_collect(*args)
            processed[0] += 1
            return result

        frames, errors = [], []
        video.frame_ready.connect(frames.append)
        video.event_ready.connect(errors.append)
        step = [0]

        def read():
            count = step[0]
            if 0 < count < 5:
                wait_until(lambda: processed[0] >= count)
            if count == 5:
                self.assertTrue(save_started.wait(3))
            if count == 10:
                # JPEG 저장을 막아 놓은 동안에도 영상 5프레임이 더 전달됐다.
                self.assertEqual(len(frames), 10)
                self.assertFalse(saved.is_set())
                release_save.set()
                self.assertTrue(saved.wait(3))
                video.request_stop()
            step[0] += 1
            return True, self.frame.copy()

        video.stream.read.side_effect = read
        with patch('workers.video_worker.PersonTracker', return_value=tracker), \
                patch.object(self.worker.collector, '_save', side_effect=slow_save), \
                patch.object(self.worker.collector, 'collect_image', side_effect=collect):
            video.run()
        self.assertTrue(video.cleanup_ok)
        self.assertFalse(self.worker.is_alive())
        self.assertNotEqual(save_thread_ids, [get_ident()])
        self.assertIsNone(video.vlm_worker)
        self.assertIsNone(video.recording_manager)
        self.assertEqual(len(frames), 11)
        self.assertFalse(errors)
        files = list(Path(self.temp.name).rglob('*.jpg'))
        self.assertEqual(len(files), 5)
        _, expected = cv2.imencode('.jpg', self.frame[20:190, 20:150])
        self.assertEqual(files[0].read_bytes(), expected.tobytes())


if __name__ == '__main__':
    unittest.main()
