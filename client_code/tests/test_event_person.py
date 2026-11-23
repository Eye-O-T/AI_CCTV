"""인물 이벤트의 의존 순서·종료 사유·경로 경합을 임시 자료로 검증한다."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import cv2
import numpy as np
from test_event_camera import APP, Session, backend, CAM, wait_for
from events.coordinator import EventCoordinator
from cameras.runtime_manager import CameraRuntimeManager, CameraRun
from reid.tasks import Observation
from storage.clip_manager import ClipManager, PersonMediaUpdate
from workers.video_worker import VideoWorker
from workers.cnn_worker import CNNWorker


class ManualStore:
    def __init__(self, *_):
        self.jobs, self.results, self.rows = [], [], {}
        self.alive, self.reject = True, False
        self.finish_calls = 0
    def start(self): pass
    def set_access_token(self, _): pass
    def submit(self, method, path, body, kind, camera_id):
        if self.reject:
            return None
        request_id = str(len(self.jobs) + 1)
        self.jobs.append(dict(request_id=request_id, method=method, path=path, body=body, kind=kind))
        return request_id
    def take_results(self):
        results, self.results = self.results, []
        return results
    def is_alive(self): return self.alive
    def finish(self, _=8):
        self.finish_calls += 1
        self.alive = False
    def abort(self): self.alive = False


class PersonQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'AI_CCTV'
        self.root.mkdir()
        self.session = Session('http://unused', 'test-token')
        self.store = ManualStore()
        self.coordinator = EventCoordinator(self.session, store_factory=lambda *_: self.store)
        self.addCleanup(self.coordinator.deleteLater)
        self.addCleanup(self.coordinator.timer.stop)
        self.reply(self.store.jobs[0], {'storage_root': str(self.root), 'locked': False})
        self.execution = uuid4().hex
        self.first = datetime(2026, 11, 19, 3, tzinfo=timezone.utc)
        self.coordinator.camera_event('camera_started', 7, self.execution, self.first)
        self.reply(self.store.jobs[-1])
        self.task = uuid4()

    @property
    def jobs(self): return self.store.jobs[2:]

    def snapshot(self, **changes):
        return replace(PersonMediaUpdate(7, self.execution, 0, 1, self.root / 'folder',
            self.first.replace(tzinfo=None), (self.first + timedelta(seconds=2)).replace(tzinfo=None),
            None, None, None, None, self.task, 12, False, self.first,
            self.first + timedelta(seconds=2), None, 1), **changes)

    def path(self, folder, name):
        return self.root / f'이벤트/person_12/2026-11-19/camera_7/{folder}/{name}'

    def reply(self, job, payload=None, error=''):
        if payload is None and not error:
            body = job['body']
            if job['method'] == 'POST':
                payload = dict.fromkeys(('tracking_generation', 'local_person_id', 'global_person_id',
                                         'related_event_id', 'crop_path', 'clip_path', 'trajectory_path')) | body
                payload.update(event_id=len(self.store.rows) + 1, server_run_id=1)
                self.store.rows[payload['event_id']] = dict(payload)
            else:
                event_id = int(job['path'].split('/')[-2])
                self.store.rows[event_id].update(body)
                payload = dict(self.store.rows[event_id])
        self.store.results.append(dict(request_id=job['request_id'], payload=payload, error=error))
        self.coordinator.poll()

    def test_appearance_then_latest_media_then_related_disappearance(self):
        initial = self.snapshot(crop_path=self.path('old', 'crop.jpg'))
        self.coordinator.person_update(initial)
        create = self.jobs[-1]
        final = replace(initial, revision=4, finished_at=initial.last_seen, finish_reason='natural',
                        crop_path=self.path('final', 'crop.jpg'), clip_path=self.path('final', 'clip.mp4'),
                        trajectory_path=self.path('final', 'trajectory.jpg'))
        self.coordinator.person_update(final)
        self.assertEqual(len(self.jobs), 1)
        self.reply(create)
        media = self.jobs[-1]
        self.assertEqual(media['method'], 'PATCH')
        self.assertEqual(len(media['body']), 3)
        self.assertTrue(all('/final/' in path for path in media['body'].values()))
        self.reply(media)
        gone = self.jobs[-1]
        self.assertEqual(gone['body']['event_type'], 'person_disappeared')
        self.assertEqual(gone['body']['occurred_at'], final.last_seen_utc.isoformat())
        self.assertEqual(gone['body']['related_event_id'], 2)
        self.reply(gone)
        self.coordinator.person_update(initial)
        self.coordinator.person_update(final)
        self.assertEqual(len(self.jobs), 3)
        self.assertFalse(self.coordinator._persons)
        self.assertFalse(self.coordinator.failures)

    def test_unmatched_wrong_execution_task_global_id_and_old_revision_are_ignored(self):
        initial = self.snapshot()
        self.coordinator.person_update(replace(initial, global_person_id=None))
        self.coordinator.person_update(replace(initial, execution_id=uuid4().hex))
        self.assertFalse(self.jobs)
        self.coordinator.person_update(initial)
        self.coordinator.person_update(replace(initial, revision=2, crop_task_id=uuid4()))
        self.coordinator.person_update(replace(initial, revision=2, global_person_id=99))
        self.coordinator.person_update(replace(initial, revision=0))
        self.reply(self.jobs[0])
        self.assertEqual(len(self.jobs), 1)

    def test_failed_create_never_sends_dependent_patch_or_disappearance(self):
        initial = self.snapshot()
        self.coordinator.person_update(initial)
        self.coordinator.person_update(replace(initial, revision=2, finished_at=initial.last_seen, finish_reason='natural'))
        self.reply(self.jobs[0], error='CONNECTION_ERROR')
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.coordinator.failures, 1)
        self.assertFalse(self.coordinator._persons)

    def test_rename_rejected_create_reconfirms_identity_without_stale_paths_once(self):
        initial = self.snapshot(crop_path=self.path('old', 'crop.jpg'))
        self.coordinator.person_update(initial)
        self.coordinator.person_update(replace(initial, revision=2, crop_path=self.path('new', 'crop.jpg')))
        self.reply(self.jobs[0], error='REQUEST_REJECTED')
        fallback = self.jobs[-1]
        self.assertIsNone(fallback['body']['crop_path'])
        for field in ('camera_id', 'camera_execution_id', 'tracking_generation', 'local_person_id', 'global_person_id', 'occurred_at'):
            self.assertEqual(fallback['body'][field], self.jobs[0]['body'][field])
        self.reply(fallback)
        self.assertIn('/new/', self.jobs[-1]['body']['crop_path'])
        self.reply(self.jobs[-1])
        self.assertEqual(len(self.jobs), 3)
        self.assertFalse(self.coordinator.failures)

    def test_patch_rename_race_uses_newer_revision_and_never_reverts(self):
        initial = self.snapshot()
        self.coordinator.person_update(initial)
        self.reply(self.jobs[0])
        self.coordinator.person_update(replace(initial, revision=2, crop_path=self.path('old', 'crop.jpg')))
        old_patch = self.jobs[-1]
        latest = replace(initial, revision=4, crop_path=self.path('new', 'crop.jpg'))
        self.coordinator.person_update(latest)
        self.reply(old_patch, error='REQUEST_REJECTED')
        self.assertIn('/new/', self.jobs[-1]['body']['crop_path'])
        self.reply(self.jobs[-1])
        self.coordinator.person_update(replace(initial, revision=3, crop_path=self.path('old', 'crop.jpg')))
        self.assertEqual(len(self.jobs), 3)
        self.assertFalse(self.coordinator.failures)

    def test_delayed_incremental_patch_cannot_clear_later_completed_media(self):
        initial = self.snapshot()
        self.coordinator.person_update(initial)
        self.reply(self.jobs[0])
        crop = replace(initial, revision=2, crop_path=self.path('live', 'crop.jpg'))
        self.coordinator.person_update(crop)
        old = self.jobs[-1]
        self.assertEqual(set(old['body']), {'crop_path'})
        final = replace(crop, revision=3, clip_path=self.path('live', 'clip.mp4'))
        self.coordinator.person_update(final)
        self.reply(old)
        self.reply(self.jobs[-1])
        saved_clip = self.store.rows[2]['clip_path']
        self.store.rows[2].update(old['body'])  # 응답 유실 후 원래 서버 요청이 늦게 끝난 경우
        self.assertEqual(self.store.rows[2]['clip_path'], saved_clip)
        self.assertIsNotNone(saved_clip)

    def test_cleanup_gap_reset_and_finished_at_alone_do_not_disappear(self):
        for person_id, reason in enumerate(('cleanup', 'stream_gap', 'tracking_reset', None), 1):
            update = self.snapshot(local_person_id=person_id, finished_at=self.first.replace(tzinfo=None), finish_reason=reason)
            self.coordinator.person_update(update)
            self.reply(self.jobs[-1])
        self.assertEqual(len(self.jobs), 4)
        self.assertTrue(all(j['body']['event_type'] == 'person_appeared' for j in self.jobs))

    def test_partial_move_uses_only_final_paths_and_shutdown_waits_for_dependencies(self):
        initial = self.snapshot(crop_path=self.path('live', 'crop.jpg'), clip_path=Path(self.temp.name) / 'temp/clip.mp4', move_failed=True)
        self.coordinator.person_update(initial)
        self.assertIsNone(self.jobs[0]['body']['clip_path'])
        self.assertTrue(self.jobs[0]['body']['crop_path'].startswith('이벤트/'))
        final = replace(initial, revision=2, finished_at=initial.last_seen, finish_reason='natural',
                        crop_path=self.path('done', 'crop.jpg'), trajectory_path=self.path('done', 'trajectory.jpg'))
        self.coordinator.person_update(final)
        self.coordinator.end_execution(7, self.execution)
        self.coordinator.begin_shutdown()
        self.assertEqual(self.store.finish_calls, 0)
        self.reply(self.jobs[0])
        self.assertEqual(self.store.finish_calls, 0)
        self.reply(self.jobs[-1])
        self.assertEqual(self.store.finish_calls, 0)
        self.reply(self.jobs[-1])
        self.assertTrue(self.coordinator.finished)
        self.assertFalse(self.coordinator.failures)

    def test_rejected_create_fallback_is_bounded_and_queue_failure_is_reported(self):
        update = self.snapshot(crop_path=self.path('old', 'crop.jpg'), finished_at=self.first.replace(tzinfo=None))
        self.coordinator.person_update(update)
        self.reply(self.jobs[0], error='REQUEST_REJECTED')
        self.reply(self.jobs[-1], error='REQUEST_REJECTED')
        self.assertEqual(len(self.jobs), 2)
        self.assertEqual(self.coordinator.failures, 1)
        self.store.reject = True
        self.coordinator.person_update(replace(update, local_person_id=2))
        self.assertEqual(self.coordinator.failures, 2)

    def test_failed_media_is_reported_but_disappearance_still_references_saved_appearance(self):
        initial = self.snapshot()
        self.coordinator.person_update(initial)
        self.reply(self.jobs[0])
        final = replace(initial, revision=2, finished_at=initial.last_seen, finish_reason='natural',
                        crop_path=self.path('final', 'crop.jpg'))
        self.coordinator.person_update(final)
        self.reply(self.jobs[-1], error='REQUEST_REJECTED')
        self.assertEqual(self.jobs[-1]['body']['event_type'], 'person_disappeared')
        self.assertEqual(self.jobs[-1]['body']['related_event_id'], 2)
        self.reply(self.jobs[-1])
        self.assertEqual(self.coordinator.failures, 1)

    def test_runtime_drains_final_buffer_while_stopping_and_shutting_down(self):
        worker = VideoWorker(camera_id=7, use_yolo=False)
        worker.execution_id = self.execution
        worker.first_frame_at = self.first
        worker.cleanup_finished_at = self.first + timedelta(seconds=3)
        worker.cleanup_ok = worker.stop_requested = True
        final = self.snapshot(finished_at=self.first.replace(tzinfo=None), finish_reason='cleanup')
        worker._capture_person_media(final)
        runtime = CameraRuntimeManager()
        runtime.attach_event_store(self.coordinator)
        runtime.runs[7] = CameraRun(CAM | {'camera_id': 7}, {}, 1, 0, worker=worker,
                                   pending=False, stopping=True, start_event_attempted=True)
        runtime.shutting_down = True
        runtime._poll_stop()
        self.assertEqual(self.jobs[0]['kind'], 'person_appear')
        self.assertEqual(self.jobs[-1]['kind'], 'camera_stopped')
        self.assertFalse(runtime.runs)
        runtime.deleteLater()


class WorkerPersonBoundaryTests(unittest.TestCase):
    def test_natural_and_gap_expiry_have_distinct_terminal_reasons(self):
        for natural in (True, False):
            with self.subTest(natural=natural), TemporaryDirectory() as tmp:
                worker = VideoWorker(camera_id=1, use_yolo=False)
                root = Path(tmp) / 'AI_CCTV'
                manager = ClipManager(Path(tmp) / 'temp', camera_id=1, execution_id=worker.execution_id,
                                      destination_root=root, on_update=worker._capture_person_media)
                worker.clip_manager = manager
                frame = np.zeros((64, 64, 3), np.uint8)
                source = Path(tmp) / 'crop.jpg'
                source.write_bytes(cv2.imencode('.jpg', frame)[1].tobytes())
                manager.update_person(1, frame, (1, 1, 50, 60))
                task = uuid4()
                manager.save_representative_crop(1, source, 0, task)
                manager.assign_global_person(Observation(task, 1, worker.execution_id, 0, 1, datetime.now(timezone.utc)), 1)
                worker.state_manager.update_person(1, (1, 1, 50, 60), True)
                worker.state_manager.person_states[1]['last_seen'] -= 4
                last_seen_utc = manager.person_clips[1]['last_seen_utc']
                worker._expire_persons(natural=natural)
                updates, failed = worker.take_person_event_updates()
                self.assertFalse(failed)
                self.assertEqual(updates[-1].finish_reason, 'natural' if natural else 'stream_gap')
                self.assertIsNotNone(updates[-1].first_seen_utc.tzinfo)
                self.assertEqual(updates[-1].last_seen_utc, last_seen_utc)
                self.assertFalse(manager.person_clips)

    def test_generation_reset_closes_old_media_and_clears_detection_state(self):
        with TemporaryDirectory() as tmp:
            worker = VideoWorker(use_yolo=False)
            worker.clip_manager = MagicMock()
            worker.tracker = SimpleNamespace(tracking_generation=1)
            worker.state_manager.update_person(1, (1, 1, 10, 20), True)
            worker._collect_crops(np.zeros((32, 32, 3), np.uint8), [])
            worker.clip_manager.finish_all.assert_called_once_with(reason='tracking_reset')
            self.assertFalse(worker.state_manager.person_states)
            self.assertEqual(worker._crop_generation, 1)

    def test_late_reid_result_is_discarded_without_appearance(self):
        worker = VideoWorker(use_yolo=False)
        worker.osnet_worker = MagicMock(accepting=True)
        observation = Observation(uuid4(), 1, worker.execution_id, 0, 1, datetime.now(timezone.utc))
        worker.osnet_worker.take_results.return_value = [SimpleNamespace(observation=observation, global_person_id=1)]
        worker._poll_reid_results()
        self.assertEqual(worker.take_person_event_updates(), ([], False))
        self.assertFalse(worker.global_person_ids)

    def test_worker_metadata_buffer_coalesces_revisions_and_reports_overflow(self):
        worker = VideoWorker(use_yolo=False)
        now = datetime.now(timezone.utc)
        update = PersonMediaUpdate(1, worker.execution_id, 0, 1, Path('temp'), now, now, None,
                                   None, None, None, uuid4(), 1, False, now, now, None, 4)
        worker._capture_person_media(update)
        worker._capture_person_media(replace(update, revision=2))
        updates, overflow = worker.take_person_event_updates()
        self.assertEqual(updates, [update])
        self.assertFalse(overflow)
        for local in range(129):
            worker._capture_person_media(replace(update, local_person_id=local + 1))
        updates, overflow = worker.take_person_event_updates()
        self.assertEqual(len(updates), 128)
        self.assertTrue(overflow)


class PersonHTTPTests(unittest.TestCase):
    def run_flow(self, natural=True, race=False, partial=False):
        with backend() as (root, session, client):
            created = client.post('/api/v1/global-persons', json={'request_id': str(uuid4()),
                'feature_vector': [1.] + [0.] * 511, 'observed_at': datetime.now(timezone.utc).isoformat()})
            global_id = created.json()['global_person_id']
            # 이 회귀는 미디어/이벤트 순서가 목적이다. 실제 CNN은 test_cnn_pipeline에서 검증한다.
            cnn_result = {'model_version': 'exp3', 'attributes': {
                'gender': 'Male', 'age': '청년', 'hat': 'No Hat',
                'upper_color': ['Black'], 'lower_color': ['Blue'],
                'sleeve': 'Short Sleeve', 'lower_type': 'Long Pants', 'carrying': []}}
            co = EventCoordinator(session, cnn_factory=lambda: CNNWorker(
                lambda: SimpleNamespace(analyze=lambda _: cnn_result)))
            runtime = CameraRuntimeManager()
            runtime.bind_session(session)
            runtime.attach_event_store(co)
            worker = VideoWorker(camera_id=1, use_yolo=False)
            manager = None
            gate, release = Event(), Event()
            try:
                wait_for(lambda: co.loaded)
                co.set_root(str(root))
                wait_for(lambda: co.ready)
                worker.first_frame_at = datetime.now(timezone.utc)
                run = CameraRun(CAM, {}, 1, 0, worker=worker, pending=False)
                runtime.runs[1] = run
                worker.media_ready.connect(lambda _: runtime._drain_person_events(run))
                runtime._record_camera_start(run)
                frame = np.random.default_rng(4).integers(0, 256, (240, 320, 3), dtype=np.uint8)
                source_dir = root.parent / 'osnet-originals'
                source_dir.mkdir()
                data = cv2.imencode('.jpg', frame)[1].tobytes()
                sources = [source_dir / f'{index}.jpg' for index in range(5)]
                for source in sources:
                    source.write_bytes(data)
                hashes = [hashlib.sha256(p.read_bytes()).digest() for p in sources]
                manager = ClipManager(root.parent / 'temp', camera_id=1, execution_id=worker.execution_id,
                    fps=10, destination_root=root, on_update=worker._capture_person_media)
                worker.clip_manager = manager
                manager.update_person(1, frame, (20, 20, 160, 200))
                task = uuid4()
                manager.save_representative_crop(1, sources[0], 0, task)
                observed = datetime.now(timezone.utc) + timedelta(seconds=1)  # 첫 탐지와 다른 crop 촬영 시각
                observation = Observation(task, 1, worker.execution_id, 0, 1, observed)
                result = SimpleNamespace(observation=observation, global_person_id=global_id, mode='new', similarity=None)
                worker.osnet_worker = MagicMock(accepting=True)
                worker.osnet_worker.take_results.return_value = [result]
                original_request = co.store._request
                def request(http, job):
                    if race and job['kind'] == 'person_appear' and not gate.is_set():
                        gate.set()
                        if not release.wait(5):
                            raise RuntimeError('Test release timeout')
                    return original_request(http, job)
                with patch.object(co.store, '_request', side_effect=request), patch.object(worker.crop_worker, 'is_result_current', return_value=True):
                    worker._poll_reid_results()
                    first_utc = manager.person_clips[1]['first_seen_utc']
                    last_utc = manager.person_clips[1]['last_seen_utc']
                    if race:
                        wait_for(gate.is_set)
                    else:
                        wait_for(lambda: not co._pending)
                    if partial:
                        rename = os.rename
                        def fail_clip(source, destination):
                            if Path(source).name == 'clip.mp4':
                                raise OSError('simulated clip move failure')
                            return rename(source, destination)
                        with patch('storage.clip_manager.os.rename', side_effect=fail_clip):
                            manager.finish_person(1, reason='natural' if natural else 'cleanup')
                    else:
                        manager.finish_person(1, reason='natural' if natural else 'cleanup')
                    worker.cleanup_ok = worker.stop_requested = True
                    worker.cleanup_finished_at = datetime.now(timezone.utc)
                    runtime.stop(1)
                    runtime._poll_stop()
                    runtime.begin_shutdown()
                    release.set()
                    wait_for(lambda: co.finished, seconds=10)
                self.assertFalse(co.failures)
                rows = client.get('/api/v1/events').json()['items']
                appearances = [r for r in rows if r['event_type'] == 'person_appeared']
                disappearances = [r for r in rows if r['event_type'] == 'person_disappeared']
                self.assertEqual(len(appearances), 1)
                self.assertEqual(len(disappearances), int(natural))
                row = appearances[0]
                self.assertEqual(datetime.fromisoformat(row['occurred_at']), first_utc)
                self.assertNotEqual(datetime.fromisoformat(row['occurred_at']), observed)
                if natural:
                    self.assertEqual(disappearances[0]['related_event_id'], row['event_id'])
                    self.assertEqual(datetime.fromisoformat(disappearances[0]['occurred_at']), last_utc)
                    self.assertTrue(all(disappearances[0][name] is None for name in ('crop_path', 'clip_path', 'trajectory_path')))
                for name in ('crop_path', 'clip_path', 'trajectory_path'):
                    if partial and name == 'clip_path':
                        self.assertIsNone(row[name])
                    else:
                        self.assertIn('~', row[name])
                        self.assertTrue((root / row[name]).is_file(), row[name])
                        if name != 'clip_path':
                            self.assertIsNotNone(cv2.imdecode(np.frombuffer((root / row[name]).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR))
                        else:
                            capture = cv2.VideoCapture(str(root / row[name]))
                            try:
                                self.assertTrue(capture.read()[0])
                            finally:
                                capture.release()
                if partial:
                    remaining = list((root.parent / 'temp').rglob('clip.mp4'))
                    self.assertEqual(len(remaining), 1)
                    self.assertGreater(remaining[0].stat().st_size, 0)
                self.assertEqual([hashlib.sha256(p.read_bytes()).digest() for p in sources], hashes)
                self.assertEqual({r['camera_execution_id'] for r in rows}, {str(UUID(worker.execution_id))})
            finally:
                release.set()
                if manager:
                    manager.finish_all()
                co._abort_cnn()
                if co.cnn_worker is not None:
                    co.cnn_worker.join(5)
                co.store.abort()
                co.store.join(5)
                co.timer.stop()
                runtime._stop_timer.stop()
                runtime.deleteLater()
                co.deleteLater()
                APP.processEvents()

    def test_real_files_and_http_natural_appearance_media_disappearance(self):
        self.run_flow()

    def test_post_rename_race_and_shutdown_dependency_drain(self):
        self.run_flow(race=True)

    def test_stop_only_updates_appearance_media_without_disappearance(self):
        self.run_flow(natural=False)

    def test_partial_move_preserves_temp_and_registers_other_completed_files(self):
        self.run_flow(partial=True)


if __name__ == '__main__':
    unittest.main()
