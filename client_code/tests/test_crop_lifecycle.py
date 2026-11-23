"""두 카메라의 crop 격리, 추적 종료·초기화, STOP·재시작·실패 조건 검증."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from test_camera_runtime import CAM, FakeCameraApi
from test_client_auth import APP, wait_for
from test_crop_worker import wait_until
from cameras.runtime_manager import CameraRuntimeManager
from workers.crop_worker import CropWorker
from workers.video_worker import VideoWorker


SECOND = CAM | dict(camera_id=8, name='두 번째')
BOX = (20, 20, 150, 190)


def image(seed):
    return np.random.default_rng(seed).integers(0, 256, (240, 320, 3), dtype=np.uint8)


class CropLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workers = []
        self.addCleanup(self.stop_all)

    def make_worker(self, camera_id):
        worker = CropWorker(camera_id, output_root=self.temp.name, sample_interval=0)
        self.workers.append(worker)
        return worker

    def stop_all(self):
        for worker in self.workers:
            worker.request_stop()
        for worker in self.workers:
            if worker.ident is not None:
                worker.join(timeout=3)
            self.assertFalse(worker.is_alive())

    def submit_five(self, worker, frame, person_id=1):
        for _ in range(5):
            self.assertTrue(worker.submit(person_id, frame, BOX))

    def result(self, worker):
        wait_until(lambda: not worker.results.empty())
        return worker.take_results()

    def assert_saved(self, paths, expected):
        _, encoded = cv2.imencode('.jpg', expected[20:190, 20:150])
        self.assertEqual(len(paths), 5)
        for path in paths:
            self.assertEqual(path.read_bytes(), encoded.tobytes())

    def test_two_cameras_with_same_id_have_independent_pixels_and_paths(self):
        first, second = self.make_worker(7), self.make_worker(8)
        first.start()
        second.start()
        self.submit_five(first, image(7))
        self.submit_five(second, image(8))
        a, b = self.result(first)[0], self.result(second)[0]
        self.assert_saved(a['paths'], image(7))
        self.assert_saved(b['paths'], image(8))
        self.assertTrue(all('camera_7' in p.parts for p in a['paths']))
        self.assertTrue(all('camera_8' in p.parts for p in b['paths']))

    def test_stop_during_save_discards_queue_and_other_camera_keeps_saving(self):
        first, second = self.make_worker(7), self.make_worker(8)
        started, release = Event(), Event()
        self.addCleanup(release.set)
        original = first.collector._save

        def blocked_save(*args):
            started.set()
            if not release.wait(3):
                raise OSError('test gate timed out')
            return original(*args)

        with patch.object(first.collector, '_save', side_effect=blocked_save):
            first.start()
            second.start()
            self.submit_five(first, image(1))
            self.assertTrue(started.wait(3))
            self.submit_five(first, image(2), person_id=2)
            first.request_stop()
            self.assertTrue(first.is_alive())
            self.assertTrue(first.tasks.empty())
            self.assertFalse(first.submit(3, image(3), BOX))
            self.submit_five(second, image(8))
            self.assert_saved(self.result(second)[0]['paths'], image(8))
            release.set()
            first.join(timeout=3)
        self.assertFalse(first.is_alive())
        self.assertFalse(first.collector.collections)
        self.assertEqual(len(list((Path(self.temp.name) / 'camera_7').rglob('*.jpg'))), 5)
        restarted = self.make_worker(7)
        restarted.start()
        self.submit_five(restarted, image(9))
        self.assert_saved(self.result(restarted)[0]['paths'], image(9))
        self.submit_five(second, image(10), person_id=2)
        self.assert_saved(self.result(second)[0]['paths'], image(10))
        self.assertEqual(len(list((Path(self.temp.name) / 'camera_7').rglob('*.jpg'))), 10)

    def check_invalidation_during_quality_check(self, reset):
        worker = self.make_worker(7)
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        worker.start()
        for _ in range(4):
            worker.submit(1, image(1), BOX)
        wait_until(lambda: 1 in worker.collector.collections
                   and len(worker.collector.collections[1].images) == 4)
        original = worker.full_body_checker.is_full_body_visible
        blocked = [False]

        def check(*args):
            if not blocked[0]:
                blocked[0] = True
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test gate timed out')
            return original(*args)

        with patch.object(worker.full_body_checker, 'is_full_body_visible', side_effect=check):
            worker.submit(1, image(1), BOX)
            self.assertTrue(entered.wait(3))
            worker.submit(1, image(1), BOX)  # 아직 처리하지 않은 이전 관측도 폐기해야 한다.
            if reset:
                worker.reset(1)
            else:
                worker.remove_person(1)
            self.assertTrue(worker.tasks.empty())
            self.submit_five(worker, image(2))
            release.set()
            result = self.result(worker)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['generation'], 1 if reset else 0)
        self.assert_saved(result[0]['paths'], image(2))
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.jpg'))), 5)

    def test_expired_observation_cannot_contaminate_reused_local_id(self):
        self.check_invalidation_during_quality_check(reset=False)

    def test_generation_reset_discards_inflight_quality_and_old_candidates(self):
        self.check_invalidation_during_quality_check(reset=True)

    def test_partial_candidates_are_discarded_on_stop(self):
        worker = self.make_worker(7)
        worker.start()
        for _ in range(4):
            worker.submit(1, image(1), BOX)
        wait_until(lambda: 1 in worker.collector.collections
                   and len(worker.collector.collections[1].images) == 4)
        worker.request_stop()
        worker.join(timeout=3)
        self.assertFalse(worker.collector.collections)
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    def test_stop_during_sharpness_check_does_not_start_new_save(self):
        worker = self.make_worker(7)
        worker.start()
        for _ in range(4):
            worker.submit(1, image(1), BOX)
        wait_until(lambda: 1 in worker.collector.collections
                   and len(worker.collector.collections[1].images) == 4)
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        laplacian = cv2.Laplacian

        def blocked_quality(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test gate timed out')
            return laplacian(*args)

        with patch('storage.crop_collector.cv2.Laplacian', side_effect=blocked_quality):
            worker.submit(1, image(1), BOX)
            self.assertTrue(entered.wait(3))
            worker.request_stop()
            release.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    def test_stale_generation_error_is_not_reported_to_current_video(self):
        video = VideoWorker(use_yolo=False)
        video._crop_generation = 1
        errors = []
        video.event_ready.connect(errors.append)
        video.crop_worker.results.put(dict(generation=0, error='old generation', paths=()))
        video._poll_crop_results()
        self.assertFalse(errors)
        video.crop_worker.results.put(dict(generation=1, error='current generation', paths=()))
        video._poll_crop_results()
        self.assertEqual([e['message'] for e in errors], ['current generation'])

    def test_failed_camera_does_not_block_other_camera_or_next_person(self):
        first, second = self.make_worker(7), self.make_worker(8)
        original = first.collector._save

        def save(person_id, state):
            if person_id == 1:
                raise PermissionError('private path must not reach result')
            return original(person_id, state)

        with patch.object(first.collector, '_save', side_effect=save):
            first.start()
            second.start()
            self.submit_five(first, image(1))
            self.submit_five(second, image(8))
            failed = self.result(first)[0]
            self.assertFalse(failed['paths'])
            self.assertIn('실패', failed['error'])
            self.assertNotIn('private', failed['error'])
            self.assert_saved(self.result(second)[0]['paths'], image(8))
            self.submit_five(first, image(2), person_id=2)
            self.assert_saved(self.result(first)[0]['paths'], image(2))


class CropRuntimeLifecycleTests(unittest.TestCase):
    def test_qt_stop_waits_for_crop_save_and_restart_keeps_other_video_running(self):
        with TemporaryDirectory() as temp:
            runtime = CameraRuntimeManager(api_factory=FakeCameraApi)
            runtime.bind_session(object())
            frames = {7: 0, 8: 0}
            workers = []
            entered, release = Event(), Event()
            runtime.frame_ready.connect(lambda cid, frame: frames.__setitem__(cid, frames[cid] + 1))

            def factory(**kwargs):
                video = VideoWorker(**kwargs)
                video.crop_worker = CropWorker(kwargs['camera_id'], output_root=temp, sample_interval=0)
                frame = image(kwargs['camera_id'] + len(workers))

                class SyntheticStream:
                    is_rtsp = False
                    def open(self):
                        return True
                    def read(self):
                        time.sleep(.01)
                        return True, frame.copy()
                    def pop_connection_events(self):
                        return []
                    def release(self):
                        return True

                video.stream = SyntheticStream()
                if not workers:
                    original = video.crop_worker.collector._save
                    def blocked_save(*args):
                        entered.set()
                        if not release.wait(5):
                            raise OSError('test gate timed out')
                        return original(*args)
                    video.crop_worker.collector._save = blocked_save
                workers.append(video)
                return video

            def launch(camera):
                self.assertTrue(runtime.request_detail(camera, 'start'))
                runtime.runs[camera['camera_id']].api.completed.emit(camera, '', '')

            tracker = lambda **kw: SimpleNamespace(tracking_generation=0,
                track=lambda frame: [dict(person_id=1, bbox=BOX, conf=.9)], expire_persons=lambda ids: None)
            try:
                with patch('workers.video_worker.VideoWorker', side_effect=factory), \
                        patch('workers.video_worker.PersonTracker', side_effect=tracker):
                    launch(CAM)
                    launch(SECOND)
                    wait_for(entered.is_set)
                    runtime.stop(7)
                    runtime._poll_stop()
                    self.assertIn(7, runtime.runs)
                    self.assertFalse(runtime.can_start(CAM))
                    before = frames[8]
                    wait_for(lambda: frames[8] >= before + 5)
                    self.assertTrue(workers[0].crop_worker.is_alive())
                    release.set()
                    wait_for(lambda: 7 not in runtime.runs)
                    self.assertFalse(workers[0].crop_worker.is_alive())
                    launch(CAM)
                    wait_for(lambda: len(list((Path(temp) / 'camera_7').rglob('*.jpg'))) == 10)
                    self.assertNotEqual(workers[0].execution_id, workers[2].execution_id)
                    self.assertGreater(frames[8], before)
                    runtime.begin_shutdown()
                    wait_for(lambda: not runtime.busy)
                    self.assertTrue(all(w.cleanup_ok and not w.crop_worker.is_alive() for w in workers))
            finally:
                release.set()
                runtime.begin_shutdown()
                wait_for(lambda: not runtime.busy)
                runtime._stop_timer.stop()
                runtime.deleteLater()
                APP.processEvents()


if __name__ == '__main__':
    unittest.main()
