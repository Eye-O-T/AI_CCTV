"""임시 SQLite로 이벤트 계약·FK·중복·동시성·롤백을 확인한다. 운영 DB/장치 미사용."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from client_code.backend.database import Base, create_database
from client_code.backend.errors import APIError
from client_code.backend.models import Camera, Event, GlobalPerson, ServerRun, User
from client_code.backend.schemas.event import EventCreate, EventListQuery, EventMediaUpdate
from client_code.backend.services.event_service import EventService
from client_code.backend.services.storage_service import StorageService
from client_code.backend.schemas.storage import StorageUpdate


NOW = datetime(2026, 11, 19, 3, tzinfo=timezone.utc)
EXECUTION = str(uuid4())


def payload(**changes):
    return dict(event_type='person_appeared', camera_id=1, camera_execution_id=EXECUTION,
                tracking_generation=0, local_person_id=1, global_person_id=1,
                occurred_at=NOW) | changes


def camera_payload(**changes):
    return dict(event_type='camera_started', camera_id=1, camera_execution_id=EXECUTION,
                occurred_at=NOW) | changes


def media(folder, name):
    return f'이벤트/person_1/2026-11-19/camera_1/{folder}/{name}'


class EventDTOTests(unittest.TestCase):
    def test_four_types_and_utc(self):
        for kind in ('camera_started', 'camera_stopped'):
            EventCreate(**camera_payload(event_type=kind))
        EventCreate(**payload())
        EventCreate(**payload(event_type='person_disappeared', related_event_id=1))
        row = EventCreate(**payload(occurred_at='2026-11-19T12:00:00+09:00', camera_execution_id=EXECUTION.replace('-', '')))
        self.assertEqual(row.occurred_at, NOW)
        self.assertEqual(str(row.camera_execution_id), EXECUTION)
        self.assertEqual(row.model_dump(mode='json')['occurred_at'], '2026-11-19T03:00:00.000000Z')

    def test_invalid_types_and_combinations(self):
        bad = [dict(event_type='person_detected'), dict(camera_id=True), dict(camera_id='1'),
               dict(camera_id=0), dict(local_person_id=1.0), dict(tracking_generation=-1),
               dict(tracking_generation=True), dict(global_person_id=None), dict(local_person_id=None),
               dict(camera_execution_id='bad'), dict(camera_execution_id=None),
               dict(occurred_at='2026-11-19T03:00:00'), dict(occurred_at=123),
               dict(occurred_at='0001-01-01T00:00:00+09:00'),
               dict(server_run_id=1), dict(global_match_status='pending'), dict(related_event_id=1),
               dict(event_type='person_disappeared'),
               dict(event_type='person_disappeared', related_event_id=1, clip_path=media('a', 'clip.mp4'))]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                EventCreate(**payload(**changes))
        for field in ('tracking_generation', 'local_person_id', 'global_person_id', 'related_event_id', 'crop_path'):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                EventCreate(**camera_payload(**{field: 'a' if field == 'crop_path' else 1}))

    def test_paths_and_patch_presence(self):
        for value in ('', '/a', '../a', 'a/../b', 'a/./b', 'a//b', 'C:/a', 'C:a',
                      '\\\\host\\a', 'a\\b', 'a\x00b', 'a/b ', 'a/b.', 'a:stream', 123):
            with self.subTest(path=value), self.assertRaises(ValidationError):
                EventMediaUpdate(crop_path=value)
        for value in ({}, {'event_type': 'person_appeared'}):
            with self.assertRaises(ValidationError):
                EventMediaUpdate(**value)
        self.assertEqual(EventMediaUpdate(crop_path=None).model_dump(exclude_unset=True), {'crop_path': None})
        EventMediaUpdate(clip_path='이벤트/person_1/2026-11-19/camera_1/관측/clip.mp4')

    def test_list_bounds_and_cursor_pair(self):
        for values in ({'limit': 0}, {'limit': 101}, {'limit': True}, {'limit': '2'},
                       {'before_event_id': 1}, {'before_occurred_at': NOW},
                       {'before_event_id': 1, 'before_occurred_at': NOW.replace(tzinfo=None)}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                EventListQuery(**values)


class EventServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine, self.factory = create_database(Path(self.tmp.name) / 'events.db')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with self.factory() as db:
            db.add_all([ServerRun(status='running'), ServerRun(status='running'),
                        Camera(name='one', camera_type='webcam', webcam_index=0),
                        Camera(name='two', camera_type='webcam', webcam_index=1),
                        GlobalPerson(feature_vector=b'\x00' * 2048, last_seen_at=NOW),
                        GlobalPerson(feature_vector=b'\x00' * 2048, last_seen_at=NOW)])
            db.commit()
        self.root = Path(self.tmp.name) / 'selected' / 'AI_CCTV'
        self.root.mkdir(parents=True)
        self.storage = StorageService(self.factory, Path(self.tmp.name) / 'events.storage.json')
        self.storage.update(StorageUpdate(storage_root=str(self.root)))
        for folder in ('a', 'b', 'old', 'new'):
            for name in ('crop.jpg', 'clip.mp4', 'trajectory.jpg'):
                target = self.root / media(folder, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'test-only-media-placeholder')
        self.service = EventService(self.factory, 1, self.storage)

    def create(self, **changes):
        return self.service.create(EventCreate(**payload(**changes)))

    def count(self):
        with self.factory() as db:
            return db.scalar(select(func.count()).select_from(Event))

    def assert_api_error(self, code, callback):
        with self.assertRaises(APIError) as ctx:
            callback()
        self.assertEqual(ctx.exception.code, code)

    def test_full_flow_and_media_retry(self):
        started, _ = self.service.create(EventCreate(**camera_payload()))
        appeared, created = self.create(crop_path=media('a', 'crop.jpg'))
        self.assertTrue(created)
        updated = self.service.update_media(appeared.event_id, EventMediaUpdate(
            crop_path=media('b', 'crop.jpg'), clip_path=media('b', 'clip.mp4'), trajectory_path=media('b', 'trajectory.jpg')))
        duplicate, created = self.create(crop_path=media('a', 'crop.jpg'))
        self.assertFalse(created)
        self.assertEqual(duplicate, updated)
        disappeared, _ = self.create(event_type='person_disappeared', related_event_id=appeared.event_id,
                                     occurred_at=NOW + timedelta(seconds=5))
        self.assertEqual(disappeared.related_event_id, appeared.event_id)
        self.assertIsNone(disappeared.clip_path)
        self.service.create(EventCreate(**camera_payload(event_type='camera_stopped', occurred_at=NOW + timedelta(seconds=6))))
        self.assertEqual(self.count(), 4)
        self.assertEqual(self.service.get(started.event_id), started)
        self.assert_api_error('EVENT_MEDIA_NOT_ALLOWED', lambda: self.service.update_media(
            disappeared.event_id, EventMediaUpdate(crop_path=None)))
        cleared = self.service.update_media(appeared.event_id, EventMediaUpdate(crop_path=None))
        self.assertIsNone(cleared.crop_path)
        self.assertEqual(cleared.clip_path, updated.clip_path)

    def test_conflict_and_missing_references(self):
        self.create()
        for changes in ({'global_person_id': 2}, {'occurred_at': NOW + timedelta(seconds=1)}):
            self.assert_api_error('EVENT_REQUEST_CONFLICT', lambda: self.create(**changes))
        self.assert_api_error('CAMERA_NOT_FOUND', lambda: self.create(camera_id=999))
        self.assert_api_error('GLOBAL_PERSON_NOT_FOUND', lambda: self.create(local_person_id=2, global_person_id=999))
        self.assert_api_error('EVENT_NOT_FOUND', lambda: self.create(event_type='person_disappeared', related_event_id=999))
        self.assert_api_error('EVENT_NOT_FOUND', lambda: self.service.get(999))
        self.assert_api_error('EVENT_NOT_FOUND', lambda: self.service.update_media(999, EventMediaUpdate(clip_path=None)))
        self.assertEqual(self.count(), 1)

    def test_related_event_must_match_entire_observation_and_time(self):
        appeared, _ = self.create()
        camera, _ = self.service.create(EventCreate(**camera_payload()))
        for changes in ({'camera_id': 2}, {'camera_execution_id': str(uuid4())},
                        {'tracking_generation': 1}, {'local_person_id': 2}, {'global_person_id': 2},
                        {'occurred_at': NOW - timedelta(microseconds=1)}, {'related_event_id': camera.event_id}):
            values = dict(event_type='person_disappeared', related_event_id=appeared.event_id) | changes
            self.assert_api_error('EVENT_RELATION_CONFLICT', lambda: self.create(**values))
        other = EventService(self.factory, 2, self.storage)
        self.assert_api_error('EVENT_RELATION_CONFLICT', lambda: other.create(EventCreate(**payload(
            event_type='person_disappeared', related_event_id=appeared.event_id))))
        self.assertEqual(self.count(), 2)

    def test_identity_isolation_and_camera_idempotency(self):
        self.create()
        for changes in ({'camera_id': 2}, {'camera_execution_id': str(uuid4())},
                        {'tracking_generation': 1}, {'local_person_id': 2}):
            self.create(**changes)
        other = EventService(self.factory, 2, self.storage)
        other.create(EventCreate(**payload()))
        first, _ = self.service.create(EventCreate(**camera_payload()))
        retry, created = self.service.create(EventCreate(**camera_payload(camera_execution_id=EXECUTION.replace('-', ''))))
        self.assertFalse(created)
        self.assertEqual(first, retry)
        self.assert_api_error('EVENT_REQUEST_CONFLICT', lambda: self.service.create(EventCreate(**camera_payload(
            occurred_at=NOW + timedelta(seconds=1)))))
        self.assertEqual(self.count(), 7)

    def test_concurrent_same_key_across_service_instances(self):
        barrier = Barrier(4)
        def submit(_):
            barrier.wait()
            return EventService(self.factory, 1, self.storage).create(EventCreate(**payload()))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, range(4)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({row.event_id for row, _ in results}), 1)
        self.assertEqual(self.count(), 1)

    def test_create_and_patch_rollback_after_flush(self):
        with patch('client_code.backend.services.event_service.EventResponse.model_validate', side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(self.count(), 0)
        row, _ = self.create(crop_path=media('old', 'crop.jpg'), clip_path=media('old', 'clip.mp4'))
        with patch('client_code.backend.services.event_service.EventResponse.model_validate', side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                self.service.update_media(row.event_id, EventMediaUpdate(crop_path=media('new', 'crop.jpg'), clip_path=media('new', 'clip.mp4')))
        self.assertEqual(self.service.get(row.event_id), row)
        self.service.update_media(row.event_id, EventMediaUpdate(crop_path=media('new', 'crop.jpg'), clip_path=media('new', 'clip.mp4')))
        self.assertEqual(self.service.get(row.event_id).clip_path, media('new', 'clip.mp4'))

    def test_list_order_cursor_and_limit(self):
        for local in range(1, 6):
            self.create(local_person_id=local)
        self.create(local_person_id=6, occurred_at=NOW - timedelta(seconds=1))
        page = self.service.list(EventListQuery(limit=2)).items
        self.assertEqual([r.local_person_id for r in page], [5, 4])
        seen = page[:]
        while page:
            last = page[-1]
            page = self.service.list(EventListQuery(limit=2, before_occurred_at=last.occurred_at,
                                                    before_event_id=last.event_id)).items
            seen += page
        self.assertEqual([r.local_person_id for r in seen], [5, 4, 3, 2, 1, 6])

    def test_invalid_or_stopped_server_run(self):
        self.assert_api_error('EVENT_RUN_CONFLICT', lambda: EventService(self.factory, 999, self.storage).create(EventCreate(**payload())))
        with self.factory() as db:
            db.get(ServerRun, 1).status = 'stopped'
            db.commit()
        self.assert_api_error('EVENT_RUN_CONFLICT', lambda: self.create())
        self.assertEqual(self.count(), 0)

    def test_db_constraints_without_dto(self):
        base = payload() | {'server_run_id': 1}
        for changes in ({'event_type': 'unknown'}, {'camera_id': 999}, {'server_run_id': 999},
                        {'global_person_id': 999}, {'global_person_id': None}, {'tracking_generation': None},
                        {'tracking_generation': -1}, {'local_person_id': 0},
                        {'event_type': 'person_disappeared', 'related_event_id': 999},
                        {'event_type': 'person_disappeared', 'related_event_id': None},
                        {'event_type': 'camera_started'}, {'related_event_id': 1}):
            with self.subTest(changes=changes), self.factory() as db:
                db.add(Event(**(base | changes)))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()
        self.assertEqual(self.count(), 0)

    def test_db_unique_and_delete_restrictions(self):
        appeared, _ = self.create()
        self.create(event_type='person_disappeared', related_event_id=appeared.event_id)
        self.service.create(EventCreate(**camera_payload()))
        for values in (payload(), camera_payload()):
            with self.factory() as db:
                db.add(Event(**(values | {'server_run_id': 1})))
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()
        for model, field, value in ((Camera, Camera.camera_id, 1), (ServerRun, ServerRun.server_run_id, 1),
                                    (GlobalPerson, GlobalPerson.global_person_id, 1), (Event, Event.event_id, appeared.event_id)):
            with self.factory() as db:
                with self.assertRaises(IntegrityError):
                    db.execute(delete(model).where(field == value))
                    db.commit()
                db.rollback()
        self.assertEqual(self.count(), 3)

    def test_add_table_preserves_existing_four_and_persisted_retry(self):
        path = Path(self.tmp.name) / 'events.db'
        with self.factory() as db:
            db.add(User(username='preserved', password_hash='test-only-placeholder'))
            db.commit()
        Event.__table__.drop(self.engine)
        with closing(sqlite3.connect(path)) as db:
            before = {table: db.execute(f'SELECT * FROM {table}').fetchall()
                      for table in ('users', 'server_runs', 'cameras', 'global_person_manage')}
        Base.metadata.create_all(self.engine)
        with closing(sqlite3.connect(path)) as db:
            after = {table: db.execute(f'SELECT * FROM {table}').fetchall() for table in before}
        self.assertEqual(before, after)
        row, _ = self.create()
        self.engine.dispose()
        engine, factory = create_database(path)
        try:
            restored, created = EventService(factory, 1, StorageService(factory, self.storage.config_path)).create(EventCreate(**payload()))
            self.assertFalse(created)
            self.assertEqual(restored, row)
        finally:
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
