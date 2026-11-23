"""4단계 실제 스레드/Qt/crop 저장 연결. 카메라·운영 DB·GPU는 사용하지 않는다."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from types import SimpleNamespace
import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import numpy as np
from test_camera_runtime import CAM, FakeCameraApi
from test_client_auth import APP, wait_for
from test_global_person_manager import FakeApi, NOW, V1
from PyQt5.QtCore import QObject, pyqtSignal
from cameras.runtime_manager import CameraRuntimeManager
from reid.api_client import GlobalPersonApiError
from reid.pipeline import ReIDPipeline
from reid.tasks import Observation
from workers.crop_worker import CropWorker
from workers.video_worker import VideoWorker


class Api(FakeApi):
    def create_person(self, *args):
        try:
            return super().create_person(*args)
        except Exception as error:
            if hasattr(error, 'code'):
                raise GlobalPersonApiError(error.code, error.retryable) from None
            raise

    def set_access_token(self, token):
        self.token = token


class Extractor:
    def __init__(self):
        self.lock = Lock()
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.calls = []

    def extract(self, paths):
        with self.lock:
            self.calls.append(paths)
            self.entered.set()
            if not self.release.wait(4):
                raise RuntimeError('test timeout')
            return list(V1)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.extractor = Extractor()
        self.api = Api()
        self.pipeline = ReIDPipeline('http://test', 'test-token',
            extractor_factory=lambda: self.extractor, api_factory=lambda *args: self.api)
        self.workers = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.extractor.release.set()
        self.pipeline.request_stop()
        if self.pipeline.ident is not None:
            self.pipeline.join(6)
            self.assertFalse(self.pipeline.is_alive())
        for worker in self.workers:
            worker.request_stop()
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def worker(self, camera=1):
        worker = self.pipeline.attach(camera, uuid4().hex)
        self.workers.append(worker)
        return worker

    def submit(self, worker, local=1, generation=0):
        obs = Observation(uuid4(), worker.camera_id, worker.execution_id, generation, local, NOW)
        self.assertTrue(worker.submit(obs, [Path(f'{i}.jpg') for i in range(5)]))
        return obs

    def result(self, worker):
        values = []
        wait_for(lambda: values.extend(worker.take_results()) or bool(values))
        return values[0]

    def test_two_workers_share_one_extractor_and_global_id(self):
        first, second = self.worker(1), self.worker(2)
        self.submit(first)
        a = self.result(first)
        self.submit(second)
        b = self.result(second)
        self.assertEqual(a.global_person_id, b.global_person_id)
        self.assertEqual((a.mode, b.mode), ('new', 'existing'))
        self.assertEqual(len(self.extractor.calls), 2)
        self.assertEqual(len(self.api.rows), 1)

    def test_end_person_during_inference_cannot_register_old_result(self):
        worker = self.worker()
        self.extractor.release.clear()
        old = self.submit(worker)
        self.assertTrue(self.extractor.entered.wait(2))
        worker.end_person(1)
        new = self.submit(worker)
        self.extractor.release.set()
        result = self.result(worker)
        self.assertNotEqual(old.task_id, new.task_id)
        self.assertEqual(result.observation.task_id, new.task_id)
        self.assertEqual(len([call for call in self.api.calls if call[0] == 'create']), 1)

    def test_reset_during_inference_and_other_camera_survives(self):
        first, second = self.worker(1), self.worker(2)
        self.extractor.release.clear()
        self.submit(first)
        self.assertTrue(self.extractor.entered.wait(2))
        first.reset()
        new = self.submit(first, generation=1)
        self.submit(second)
        self.extractor.release.set()
        self.assertEqual(self.result(first).observation.task_id, new.task_id)
        self.assertEqual(self.result(second).observation.camera_id, 2)
        self.assertEqual(len(self.api.rows), 1)

    def test_camera_stop_restart_keeps_other_worker_and_registry(self):
        first, second = self.worker(1), self.worker(2)
        self.submit(first)
        old = self.result(first)
        self.pipeline.detach(first)
        first.join(2)
        self.assertFalse(first.is_alive())
        self.assertTrue(second.is_alive())
        restarted = self.worker(1)
        self.submit(restarted)
        self.assertEqual(self.result(restarted).global_person_id, old.global_person_id)
        self.submit(second)
        self.assertEqual(self.result(second).global_person_id, old.global_person_id)

    def test_queue_capacity_does_not_block_producer(self):
        worker = self.worker()
        self.extractor.release.clear()
        self.submit(worker)
        self.assertTrue(self.extractor.entered.wait(2))
        for local in range(2, 18):
            self.submit(worker, local)
        extra = Observation(uuid4(), 1, worker.execution_id, 0, 18, NOW)
        started = time.monotonic()
        self.assertFalse(worker.submit(extra, [Path(f'{i}.jpg') for i in range(5)]))
        self.assertLess(time.monotonic() - started, .1)

    def test_saved_response_loss_retry_without_reextract(self):
        self.api.lose_create = True
        worker = self.worker()
        obs = self.submit(worker)
        wait_for(lambda: self.pipeline._manager is not None and self.pipeline._manager.state == 'ERROR')
        self.assertEqual(len(self.api.rows), 1)
        self.assertTrue(self.pipeline.retry_pending())
        self.assertEqual(self.result(worker).observation.task_id, obs.task_id)
        self.assertEqual(len(self.extractor.calls), 1)
        self.assertEqual(len(self.api.rows), 1)

    def test_image_failure_does_not_create_person_or_kill_other_camera(self):
        first, second = self.worker(1), self.worker(2)
        original = self.extractor.extract
        with patch.object(self.extractor, 'extract', side_effect=RuntimeError('private-image')):
            obs = self.submit(first)
            wait_for(lambda: not first.has_observation(obs.task_id))
        self.assertEqual(self.api.rows, {})
        self.submit(second)
        self.result(second)
        self.assertTrue(first.is_alive())
        self.assertEqual(len(self.api.rows), 1)


class CropMetadataTests(unittest.TestCase):
    def test_first_accepted_crop_time_and_observation_uuid_survive(self):
        with TemporaryDirectory() as tmp:
            crop = CropWorker(1, output_root=tmp, sample_interval=0)
            frame = np.random.default_rng(50).integers(0, 256, (240, 320, 3), dtype=np.uint8)
            for i in range(6):
                with patch('workers.crop_worker.datetime') as clock:
                    clock.now.return_value = NOW + timedelta(seconds=i)
                    crop.submit(1, np.zeros_like(frame) if i == 0 else frame, (20, 20, 150, 190))
            crop.start()
            try:
                values = []
                wait_for(lambda: values.extend(crop.take_results()) or bool(values))
                result = values[0]
                self.assertEqual(result['observed_at'], NOW + timedelta(seconds=1))
                self.assertEqual(len(result['paths']), 5)
                self.assertTrue(crop.is_result_current(result))
                crop.remove_person(1)
                crop.submit(1, frame, (20, 20, 150, 190))
                self.assertFalse(crop.is_result_current(result))
                self.assertNotEqual(result['task_id'], crop._observations[1].task_id)
            finally:
                crop.request_stop()
                crop.join(3)

    def test_video_forwards_real_saved_crop_and_expires_mapping(self):
        api, extractor = Api(), Extractor()
        pipeline = ReIDPipeline('http://test', 'token', lambda: extractor, lambda *args: api)
        video = VideoWorker(camera_id=1, reid_pipeline=pipeline)
        with TemporaryDirectory() as tmp:
            video.crop_worker.collector.output_root = Path(tmp)
            video.osnet_worker = pipeline.attach(1, video.execution_id)
            video.crop_worker.sample_interval = 0
            frame = np.random.default_rng(3).integers(0, 256, (240, 320, 3), dtype=np.uint8)
            video.crop_worker.start()
            try:
                for _ in range(5):
                    video.crop_worker.submit(1, frame, (20, 20, 150, 190))
                def poll():
                    video._poll_crop_results()
                    video._poll_reid_results()
                    return 1 in video.global_person_ids
                wait_for(poll)
                self.assertEqual(len(api.rows), 1)
                self.assertEqual(len(list(Path(tmp).rglob('crop_*.jpg'))), 5)
                video.state_manager.remove_disappeared_persons = lambda: [1]
                video._expire_persons()
                self.assertNotIn(1, video.global_person_ids)
            finally:
                video.request_stop()
                video._cleanup()
                pipeline.request_stop()
                pipeline.join(4)
                self.assertTrue(video.cleanup_ok)
                self.assertFalse(video.osnet_worker.is_alive())
                video.deleteLater()


class Session(QObject):
    refresh_completed = pyqtSignal(str)
    expired = pyqtSignal(str)
    signed_out = pyqtSignal(str)
    def __init__(self):
        super().__init__()
        self.access_token = 'first-token'
        self.api = SimpleNamespace(base_url='http://test')
        self.expire_access = MagicMock()


class RuntimeReIDTests(unittest.TestCase):
    def test_two_video_threads_keep_frames_during_inference_stop_and_restart(self):
        api, extractor = Api(), Extractor()
        extractor.release.clear()
        pipeline = ReIDPipeline('http://test', 'token', lambda: extractor, lambda *args: api)
        runtime = CameraRuntimeManager(api_factory=FakeCameraApi, reid_factory=lambda *args: pipeline)
        runtime.bind_session(Session())
        frames = {7: 0, 8: 0}
        runtime.frame_ready.connect(lambda camera, frame: frames.__setitem__(camera, frames[camera] + 1))
        videos = []
        with TemporaryDirectory() as tmp:
            def factory(**kwargs):
                video = VideoWorker(**kwargs)
                video.crop_worker = CropWorker(kwargs['camera_id'], output_root=tmp, sample_interval=0)
                frame = np.random.default_rng(kwargs['camera_id']).integers(0, 256, (240, 320, 3), dtype=np.uint8)
                class Stream:
                    is_rtsp = False
                    def open(self): return True
                    def read(self):
                        time.sleep(.01)
                        return True, frame.copy()
                    def pop_connection_events(self): return []
                    def release(self): return True
                video.stream = Stream()
                videos.append(video)
                return video
            def launch(camera):
                self.assertTrue(runtime.request_detail(camera, 'start'))
                runtime.runs[camera['camera_id']].api.completed.emit(camera, '', '')
            tracker = lambda **kwargs: SimpleNamespace(tracking_generation=0,
                track=lambda frame: [dict(person_id=1, bbox=(20, 20, 150, 190), conf=.9)],
                expire_persons=lambda people: None)
            try:
                with patch('workers.video_worker.VideoWorker', side_effect=factory), \
                        patch('workers.video_worker.PersonTracker', side_effect=tracker):
                    launch(CAM)
                    launch(CAM | {'camera_id': 8, 'webcam_index': 1})
                    wait_for(lambda: extractor.entered.is_set() and min(frames.values()) > 5)
                    before = frames.copy()
                    runtime.stop(7)
                    wait_for(lambda: frames[8] >= before[8] + 4)
                    self.assertIn(7, runtime.runs)  # 해당 추론 완료 전 슬롯 재사용 금지
                    self.assertTrue(videos[1].isRunning())
                    extractor.release.set()
                    wait_for(lambda: 7 not in runtime.runs)
                    wait_for(lambda: 1 in videos[1].global_person_ids)
                    launch(CAM)
                    wait_for(lambda: 1 in videos[2].global_person_ids)
                    self.assertNotEqual(videos[0].execution_id, videos[2].execution_id)
                    self.assertEqual(videos[1].global_person_ids[1], videos[2].global_person_ids[1])
                    runtime.begin_shutdown()
                    wait_for(lambda: not runtime.busy)
                    self.assertTrue(all(video.cleanup_ok and not video.osnet_worker.is_alive() for video in videos))
            finally:
                extractor.release.set()
                runtime.begin_shutdown()
                wait_for(lambda: not runtime.busy)
                runtime._reid_timer.stop()
                runtime._stop_timer.stop()
                runtime.deleteLater()
                APP.processEvents()

    def test_shutdown_waits_for_inflight_http_and_drops_late_match(self):
        api, extractor = Api(), Extractor()
        api.release.clear()
        pipeline = ReIDPipeline('http://test', 'token', lambda: extractor, lambda *args: api)
        runtime = CameraRuntimeManager()
        runtime.reid_pipeline = pipeline
        worker = pipeline.attach(1, uuid4().hex)
        complete = []
        runtime.shutdown_complete.connect(lambda: complete.append(True))
        try:
            obs = Observation(uuid4(), 1, worker.execution_id, 0, 1, NOW)
            self.assertTrue(worker.submit(obs, [Path(f'{i}.jpg') for i in range(5)]))
            wait_for(lambda: api.entered.is_set())
            runtime.begin_shutdown()
            APP.processEvents()
            self.assertTrue(runtime.busy)
            self.assertFalse(complete)
            api.release.set()
            wait_for(lambda: not pipeline.is_alive())
            runtime._poll_reid()
            self.assertEqual(complete, [True])
            self.assertTrue(api.closed)
            self.assertEqual(worker.take_results(), [])
        finally:
            api.release.set()
            pipeline.request_stop()
            pipeline.join(5)
            runtime._reid_timer.stop()
            runtime._stop_timer.stop()
            runtime.deleteLater()

    def test_token_refresh_and_expiry_are_bridged_on_gui_thread(self):
        session = Session()
        pipeline = MagicMock()
        runtime = CameraRuntimeManager(reid_factory=lambda *args: pipeline)
        try:
            runtime.bind_session(session)
            self.assertIs(runtime._ensure_reid(), pipeline)
            session.access_token = 'second-token'
            session.refresh_completed.emit('')
            pipeline.set_access_token.assert_called_once_with('second-token')
            session.refresh_completed.emit('CONNECTION_ERROR')
            self.assertEqual(pipeline.set_access_token.call_count, 1)
            session.expired.emit('expired')
            pipeline.request_stop.assert_called_once()
            pipeline.take_notices.return_value = [(None, 'INVALID_ACCESS_TOKEN')]
            runtime._poll_reid()
            session.expire_access.assert_called_once()
        finally:
            runtime._reid_timer.stop()
            runtime._stop_timer.stop()
            runtime.deleteLater()

    def test_shutdown_waits_for_background_model_loading_without_blocking_gui(self):
        entered, release = Event(), Event()
        def load():
            entered.set()
            release.wait(4)
            return Extractor()
        pipeline = ReIDPipeline('http://test', 'token', load, lambda *args: Api())
        runtime = CameraRuntimeManager()
        runtime.reid_pipeline = pipeline
        worker = pipeline.attach(1, uuid4().hex)
        self.assertTrue(entered.wait(2))
        complete = []
        runtime.shutdown_complete.connect(lambda: complete.append(True))
        try:
            started = time.monotonic()
            runtime.begin_shutdown()
            self.assertLess(time.monotonic() - started, .1)
            APP.processEvents()
            self.assertFalse(complete)
            self.assertTrue(runtime.busy)
            release.set()
            wait_for(lambda: not pipeline.is_alive())
            runtime._poll_reid()
            self.assertEqual(complete, [True])
            self.assertFalse(worker.is_alive())
        finally:
            release.set()
            pipeline.request_stop()
            pipeline.join(5)
            runtime._reid_timer.stop()
            runtime._stop_timer.stop()
            runtime.deleteLater()

    def test_pipeline_initialization_failure_stops_workers_safely(self):
        def fail():
            raise RuntimeError('private-weights-path')
        pipeline = ReIDPipeline('http://test', 'token', fail, lambda *args: Api())
        worker = pipeline.attach(1, uuid4().hex)
        pipeline.join(3)
        self.assertFalse(pipeline.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertTrue(pipeline.failed)
        self.assertIn((None, 'REID_INITIALIZATION_FAILED'), pipeline.take_notices())
        with self.assertRaises(RuntimeError):
            pipeline.attach(2, uuid4().hex)
