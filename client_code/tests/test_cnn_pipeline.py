"""공용 CNN 큐·등장 저장 의존·폴더 rename·실제 추론/HTTP/종료 검증."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np
import test_event_person as person_fixture
from test_event_camera import APP, Session, backend, wait_for
from events.coordinator import EventCoordinator
from reid.tasks import Observation
from storage.clip_manager import ClipManager
from workers.cnn_worker import CNNWorker


RESULT = {'model_version': 'exp3', 'attributes': {
    'gender': 'Male', 'age': '청년', 'hat': 'No Hat', 'upper_color': ['Blue'],
    'lower_color': ['Black'], 'sleeve': 'Short Sleeve',
    'lower_type': 'Long Pants', 'carrying': []}}


class ManualCNN:
    def __init__(self):
        self.jobs, self.results = [], []
        self.alive, self.reject = True, False
        self.finish_calls = 0
    def start(self): pass
    def submit(self, event_id, path):
        if self.reject:
            return False
        self.jobs.append((event_id, path))
        return True
    def take_results(self):
        results, self.results = self.results, []
        return results
    def is_alive(self): return self.alive
    def finish(self): self.finish_calls += 1
    def abort(self): self.alive = False


class CNNWorkerTests(unittest.TestCase):
    def test_one_model_sequential_jobs_on_worker_thread_and_bounded_queue(self):
        calls, initialized = [], []
        def factory():
            initialized.append(get_ident())
            return SimpleNamespace(analyze=lambda path: calls.append((path, get_ident())) or RESULT)
        worker = CNNWorker(factory, capacity=2)
        self.assertTrue(worker.submit(1, 'camera-a.jpg'))
        self.assertTrue(worker.submit(2, 'camera-b.jpg'))
        self.assertFalse(worker.submit(3, 'full.jpg'))
        worker.finish()
        self.assertFalse(worker.submit(4, 'closing.jpg'))
        worker.start()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(initialized), 1)
        self.assertNotEqual(initialized[0], get_ident())
        self.assertEqual(calls, [('camera-a.jpg', initialized[0]), ('camera-b.jpg', initialized[0])])
        results = worker.take_results()
        self.assertEqual([r['event_id'] for r in results], [1, 2])
        self.assertTrue(all(r['result'] == RESULT and not r['error'] for r in results))

    def test_init_and_inference_failures_are_returned_without_retrying_model(self):
        def unavailable(): raise OSError('missing weights')
        worker = CNNWorker(unavailable)
        worker.submit(1, 'image.jpg')
        worker.start()
        worker.join(3)
        self.assertEqual(worker.take_results()[0]['error'], 'CNN_INITIALIZATION_FAILED')
        self.assertFalse(worker.submit(2, 'image.jpg'))
        def analyze(path):
            if path == 'bad': raise ValueError('invalid image')
            return RESULT
        worker = CNNWorker(lambda: SimpleNamespace(analyze=analyze))
        worker.submit(1, 'bad')
        worker.submit(2, 'good')
        worker.finish()
        worker.start()
        worker.join(3)
        results = worker.take_results()
        self.assertEqual(results[0]['error'], 'CNN_INFERENCE_FAILED')
        self.assertEqual(results[1]['result'], RESULT)

    def test_abort_discards_queued_jobs_and_inflight_result(self):
        entered, release = Event(), Event()
        calls = []
        def analyze(path):
            calls.append(path)
            entered.set()
            release.wait(3)
            return RESULT
        worker = CNNWorker(lambda: SimpleNamespace(analyze=analyze))
        worker.submit(1, 'first')
        worker.submit(2, 'queued')
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            worker.abort()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, ['first'])
        self.assertEqual(worker.take_results(), [])


class CNNCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.f = person_fixture.PersonQueueTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.co = self.f.coordinator
        self.cnn = ManualCNN()
        self.co._cnn_factory = lambda: self.cnn
        self.source = Path(self.f.temp.name) / 'output' / 'crop_01.jpg'

    def appear(self, **changes):
        update = self.f.snapshot(cnn_source_path=self.source, **changes)
        self.co.person_update(update)
        job = self.f.jobs[-1]
        self.f.reply(job)
        return update

    def test_requires_saved_appearance_and_source_then_once_after_rename(self):
        update = self.f.snapshot()
        self.co.person_update(update)
        self.assertIsNone(self.co.cnn_worker)
        self.f.reply(self.f.jobs[-1])
        self.assertIsNone(self.co.cnn_worker)
        update = replace(update, revision=2, cnn_source_path=self.source,
                         crop_path=self.f.path('before', 'crop.jpg'))
        self.co.person_update(update)
        self.assertEqual(self.cnn.jobs, [(2, self.source)])
        self.co.person_update(replace(update, revision=3, crop_path=self.f.path('after', 'crop.jpg')))
        self.assertEqual(len(self.cnn.jobs), 1)
        # 서버에 등장 생성이 거부된 다른 관측은 분석하지 않는다.
        self.co.person_update(replace(update, local_person_id=2, crop_task_id=uuid4()))
        self.f.reply(self.f.jobs[-1], error='CONNECTION_ERROR')
        self.assertEqual(len(self.cnn.jobs), 1)

    def test_finished_person_can_save_result_without_extra_timeline_card(self):
        saved = []
        self.co.event_saved.connect(saved.append)
        self.appear(finished_at=self.f.first, finish_reason='cleanup')
        self.assertFalse(self.co._persons)
        self.cnn.results.append(dict(event_id=2, result=RESULT, error=''))
        self.co.poll()
        job = self.f.jobs[-1]
        self.assertEqual(job['path'], '/api/v1/events/2/cnn-result')
        self.f.reply(job)
        self.assertEqual(len(saved), 1)
        self.assertFalse(self.co._cnn_jobs)
        self.assertFalse(self.co.failures)

    def test_inference_queue_and_http_failures_do_not_delete_events(self):
        self.appear()
        self.cnn.results.append(dict(event_id=2, result=None, error='CNN_INFERENCE_FAILED'))
        self.co.poll()
        self.assertEqual(len(self.f.jobs), 1)
        self.assertIn(2, self.f.store.rows)
        self.assertEqual(self.co.failures, 1)
        self.cnn.reject = True
        self.appear(local_person_id=2, crop_task_id=uuid4())
        self.assertEqual(self.co.failures, 2)
        self.cnn.reject = False
        self.appear(local_person_id=3, crop_task_id=uuid4())
        event_id = self.cnn.jobs[-1][0]
        self.cnn.results.append(dict(event_id=event_id, result=RESULT, error=''))
        self.co.poll()
        self.f.reply(self.f.jobs[-1], error='INVALID_ACCESS_TOKEN')
        self.assertEqual(self.co.failures, 3)
        self.assertFalse(self.co._cnn_jobs)

    def test_shutdown_waits_for_create_cnn_and_result_patch_before_store_finish(self):
        self.co.person_update(self.f.snapshot(cnn_source_path=self.source,
                              finished_at=self.f.first, finish_reason='cleanup'))
        self.co.begin_shutdown()
        self.assertEqual(self.cnn.jobs, [])
        self.f.reply(self.f.jobs[-1])
        self.assertEqual(self.cnn.jobs, [(2, self.source)])
        self.assertEqual(self.f.store.finish_calls, 0)
        self.cnn.results.append(dict(event_id=2, result=RESULT, error=''))
        self.cnn.alive = False
        self.co.poll()
        self.assertEqual(self.f.store.finish_calls, 0)
        self.f.reply(self.f.jobs[-1])
        self.assertTrue(self.co.finished)
        self.assertGreater(self.f.store.finish_calls, 0)

    def test_timeout_discards_late_result_and_waits_for_running_model(self):
        self.appear()
        self.cnn.abort = lambda: None  # 이미 실행 중인 native 추론이 즉시 반환하지 않는 경우
        self.co.begin_shutdown(timeout=0)
        # 실제 native 추론은 강제 중단하지 않고 반환 후 종료를 기다린다.
        self.assertTrue(self.co._shutdown_expired)
        self.assertFalse(self.co.finished)
        self.assertFalse(self.co._cnn_jobs)
        self.cnn.results.append(dict(event_id=2, result=RESULT, error=''))
        self.co.poll()
        self.assertEqual(len(self.f.jobs), 1)
        self.assertEqual(self.co.failures, 1)
        self.cnn.alive = False
        self.co.poll()
        self.assertTrue(self.co.finished)

    def test_two_cameras_same_global_person_have_independent_cnn_jobs(self):
        self.appear()
        execution = uuid4().hex
        self.co.camera_event('camera_started', 8, execution, self.f.first)
        self.f.reply(self.f.jobs[-1])
        other = self.f.snapshot(camera_id=8, execution_id=execution,
                                crop_task_id=uuid4(), cnn_source_path=Path('camera8/crop.jpg'))
        self.co.person_update(other)
        self.f.reply(self.f.jobs[-1])
        self.assertEqual(len(self.cnn.jobs), 2)
        self.assertNotEqual(self.cnn.jobs[0][0], self.cnn.jobs[1][0])
        self.assertEqual({value['global_person_id'] for value in self.co._cnn_jobs.values()}, {12})

    def test_signed_out_drops_old_results_and_invalid_ack_is_failure(self):
        self.appear()
        self.cnn.results.append(dict(event_id=2, result=RESULT, error=''))
        self.co.poll()
        job = self.f.jobs[-1]
        wrong = self.f.store.rows[2] | {'event_id': 999, 'cnn_result': RESULT}
        self.f.reply(job, payload=wrong)
        self.assertEqual(self.co.failures, 1)
        self.co._signed_out()
        self.cnn.results.append(dict(event_id=2, result=RESULT, error=''))
        count = len(self.f.jobs)
        self.co.poll()
        self.assertEqual(len(self.f.jobs), count)


class CNNHTTPTests(unittest.TestCase):
    def test_real_cpu_model_crop_rename_http_patch_and_shutdown(self):
        from appearance.analyzer import AppearanceAnalyzer, DEFAULT_WEIGHTS_PATH
        root_dir = Path(__file__).resolve().parents[2]
        original = root_dir / 'output/camera_1/2026-11-19/person_1_093101144/crop_01.jpg'
        if not DEFAULT_WEIGHTS_PATH.is_file() or not original.is_file():
            self.skipTest('로컬 exp3 가중치와 기존 crop 필요')
        calls, outputs = [], []
        def factory():
            analyzer = AppearanceAnalyzer(device='cpu')
            def analyze(path):
                calls.append((path, get_ident()))
                result = analyzer.analyze(path)
                outputs.append(result)
                return result
            return SimpleNamespace(analyze=analyze)
        self.run_flow(factory, calls, outputs, original)

    def run_flow(self, factory, calls, outputs, source):
        with backend() as (root, session, client):
            person = client.post('/api/v1/global-persons', json={'request_id': str(uuid4()),
                'feature_vector': [1.] + [0.] * 511,
                'observed_at': datetime.now(timezone.utc).isoformat()}).json()['global_person_id']
            co = EventCoordinator(session, cnn_factory=lambda: CNNWorker(factory))
            manager = ClipManager(root.parent / 'temp', camera_id=1, destination_root=root,
                                  on_update=co.person_update)
            try:
                wait_for(lambda: co.loaded)
                co.set_root(str(root))
                wait_for(lambda: co.ready)
                co.camera_event('camera_started', 1, manager.execution_id, datetime.now(timezone.utc))
                frame = np.zeros((240, 320, 3), dtype=np.uint8)
                manager.update_person(1, frame, (20, 20, 160, 200))
                task = uuid4()
                manager.save_representative_crop(1, source, 0, task)
                manager.assign_global_person(Observation(task, 1, manager.execution_id, 0, 1,
                                             datetime.now(timezone.utc)), person)
                # 분석 입력은 output 원본이며 이벤트 폴더 rename/관측 종료 뒤에도 유효하다.
                manager.finish_person(1, reason='natural')
                co.end_execution(1, manager.execution_id)
                co.begin_shutdown(timeout=20)
                wait_for(lambda: co.finished, seconds=25)
                self.assertFalse(co.failures)
                self.assertFalse(co.cnn_worker.is_alive())
                rows = client.get('/api/v1/events').json()['items']
                appeared = [row for row in rows if row['event_type'] == 'person_appeared']
                self.assertEqual(len(appeared), 1)
                self.assertEqual(appeared[0]['cnn_result'], outputs[0])
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0], source.resolve())
                self.assertNotEqual(calls[0][1], get_ident())
                self.assertTrue((root / appeared[0]['crop_path']).is_file())
                gone = next(row for row in rows if row['event_type'] == 'person_disappeared')
                self.assertEqual(gone['related_event_id'], appeared[0]['event_id'])
                self.assertIsNone(gone['cnn_result'])
            finally:
                manager.finish_all()
                co._abort_cnn()
                if co.cnn_worker is not None:
                    co.cnn_worker.join(10)
                co.store.abort()
                co.store.join(5)
                co.timer.stop()
                co.deleteLater()
                APP.processEvents()


if __name__ == '__main__':
    unittest.main()
