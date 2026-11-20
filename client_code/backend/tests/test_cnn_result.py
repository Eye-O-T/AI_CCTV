"""임시 DB만으로 CNN 결과 API·롤백·동시 갱신·기존 DB 마이그레이션 검증."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from client_code.backend.main import create_app
from client_code.backend.migrations import migrate_event_cnn_result
from client_code.backend.repositories.event_repository import EventRepository
from client_code.backend.tests.test_event_api import seed, login, appeared, write_media, media, WHEN


RESULT = {
    'model_version': 'exp3',
    'attributes': {
        'gender': 'Male', 'age': '청년', 'hat': 'No Hat',
        'upper_color': ['Black', 'White'], 'lower_color': ['Blue'],
        'sleeve': 'Short Sleeve', 'lower_type': 'Long Pants', 'carrying': [],
    },
}
EVENTS = '/api/v1/events'


class CNNResultAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.path = self.base / 'test.db'
        self.root = self.base / 'AI_CCTV'
        self.root.mkdir()
        self.app = create_app(self.path)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.tokens = seed(self.client)
        self.assertEqual(self.client.put('/api/v1/settings/storage',
                         json={'storage_root': str(self.root)}).status_code, 200)
        self.body = appeared()
        created = self.client.post(EVENTS, json=self.body)
        self.assertEqual(created.status_code, 201, created.text)
        self.event = created.json()
        self.target = EVENTS + f"/{self.event['event_id']}"

    def save(self, result=None, target=None):
        return self.client.patch((target or self.target) + '/cnn-result',
                                 json={'cnn_result': result if result is not None else RESULT})

    def test_save_retry_get_list_and_sql_null(self):
        self.assertIsNone(self.event['cnn_result'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT cnn_result IS NULL FROM events').fetchone(), (1,))
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.json()['cnn_result'], RESULT)
        self.assertEqual(self.save().json(), response.json())
        self.assertEqual(self.client.get(self.target).json(), response.json())
        self.assertEqual(self.client.get(EVENTS).json()['items'], [response.json()])
        # 등장 POST 재전송은 분석 결과를 초기화하거나 불변 내용 충돌을 일으키지 않는다.
        self.assertEqual(self.client.post(EVENTS, json=self.body).json(), response.json())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(json.loads(db.execute('SELECT cnn_result FROM events').fetchone()[0]), RESULT)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)

    def test_media_and_result_updates_preserve_each_other(self):
        write_media(self.root)
        changed = deepcopy(RESULT)
        changed['attributes']['age'] = '장년'
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(self.save, changed)
            b = pool.submit(self.client.patch, self.target + '/media',
                            json={'crop_path': media('관측', 'crop.jpg')})
            self.assertEqual(a.result().status_code, 200)
            self.assertEqual(b.result().status_code, 200)
        value = self.client.get(self.target).json()
        self.assertEqual(value['cnn_result'], changed)
        self.assertEqual(value['crop_path'], media('관측', 'crop.jpg'))
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.client.get(self.target).json()['crop_path'], value['crop_path'])

    def test_bad_payloads_preserve_existing_result(self):
        self.assertEqual(self.save().status_code, 200)
        invalid = [{}, {'cnn_result': None}, {'cnn_result': 'secret-invalid'},
                   {'cnn_result': RESULT, 'extra': True}]
        for field, value in [('age', '16-30'), ('age', '소녀'), ('gender', True),
                             ('upper_color', []), ('lower_color', ['Black', 'Blue']),
                             ('carrying', ['Other', 'Other']), ('hat', 'secret-invalid'),
                             ('upper_color', ['Black', 'Black'])]:
            result = deepcopy(RESULT)
            result['attributes'][field] = value
            invalid.append({'cnn_result': result})
        for result in (RESULT | {'model_version': 'exp4'},
                       RESULT | {'extra': 1},
                       RESULT | {'attributes': {'gender': 'Male'}}):
            invalid.append({'cnn_result': result})
        for body in invalid:
            with self.subTest(body=body):
                response = self.client.patch(self.target + '/cnn-result', json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn('secret-invalid', response.text)
        self.assertEqual(self.client.get(self.target).json()['cnn_result'], RESULT)
        self.assertEqual(self.client.post(EVENTS, json=appeared(cnn_result=RESULT)).status_code, 422)
        self.assertEqual(self.client.patch(self.target + '/media', json={'cnn_result': RESULT}).status_code, 422)

    def test_nonappearance_missing_and_invalid_ids(self):
        gone = self.client.post(EVENTS, json=self.body | {
            'event_type': 'person_disappeared', 'related_event_id': self.event['event_id']}).json()
        ids = [gone['event_id']]
        for kind in ('camera_started', 'camera_stopped'):
            response = self.client.post(EVENTS, json=dict(event_type=kind, camera_id=1,
                camera_execution_id=str(uuid4()), occurred_at=WHEN))
            self.assertEqual(response.status_code, 201)
            ids.append(response.json()['event_id'])
        for event_id in ids:
            target = EVENTS + f'/{event_id}'
            response = self.save(target=target)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error']['code'], 'EVENT_CNN_RESULT_NOT_ALLOWED')
            self.assertIsNone(self.client.get(target).json()['cnn_result'])
        self.assertEqual(self.save(target=EVENTS + '/9999').status_code, 404)
        for value in ('0', '-1', 'abc', str(2**63)):
            self.assertEqual(self.save(target=EVENTS + '/' + value).status_code, 422)

    def test_access_refresh_expired_and_logout(self):
        for token in (None, self.tokens['refresh_token']):
            self.client.headers.pop('Authorization', None)
            if token:
                self.client.headers['Authorization'] = 'Bearer ' + token
            self.assertEqual(self.save().status_code, 401)
        self.client.headers['Authorization'] = 'Bearer ' + self.tokens['access_token']
        auth = self.app.state.auth_service
        with patch.object(auth, 'clock', return_value=auth.clock() + 901):
            self.assertEqual(self.save().status_code, 401)
        self.client.post('/api/v1/auth/logout', json={'refresh_token': self.tokens['refresh_token']})
        self.assertEqual(self.save().status_code, 401)

    def test_failure_after_flush_rolls_back(self):
        self.save()
        original = EventRepository.update_cnn_result

        def fail(repository, db, row, result):
            original(repository, db, row, result)
            raise RuntimeError('test-only failure after flush')

        changed = deepcopy(RESULT)
        changed['attributes']['age'] = '장년'
        with patch.object(EventRepository, 'update_cnn_result', fail):
            self.assertEqual(self.save(changed).status_code, 500)
        self.assertEqual(self.client.get(self.target).json()['cnn_result'], RESULT)

    def test_db_constraint_and_restart_persistence(self):
        self.save()
        with closing(sqlite3.connect(self.path)) as db:
            for value in ('[]', 'null', '"string"', 'invalid json'):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute('UPDATE events SET cnn_result=?', (value,))
                db.rollback()
        self.client.__exit__(None, None, None)
        # 같은 TestClient를 재진입해 종료/재시작을 검증한다. addCleanup은 최종 진입을 닫는다.
        self.client.__enter__()
        self.assertEqual(self.save().status_code, 401)
        login(self.client)
        self.assertEqual(self.client.get(self.target).json()['cnn_result'], RESULT)

    def test_startup_migrates_full_old_schema_and_preserves_rows(self):
        self.client.__exit__(None, None, None)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('ALTER TABLE events DROP COLUMN cnn_result')
            preserved = {name: db.execute(f'SELECT * FROM {name}').fetchall()
                         for name in ('users', 'cameras', 'global_person_manage', 'events')}
        self.client.__enter__()
        login(self.client)
        self.assertIsNone(self.client.get(self.target).json()['cnn_result'])
        with closing(sqlite3.connect(self.path)) as db:
            for name, rows in preserved.items():
                actual = db.execute(f'SELECT * FROM {name}').fetchall()
                if name == 'events':
                    actual = [row[:-1] for row in actual]
                self.assertEqual(actual, rows)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(len(list(self.base.glob('*.bak'))), 1)
        self.assertEqual(self.save().status_code, 200)


class MigrationTests(unittest.TestCase):
    def test_existing_schema_backup_data_constraints_and_idempotence(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.db'
            with closing(sqlite3.connect(path)) as db:
                db.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);"
                    "INSERT INTO users VALUES(1, 'preserve');"
                    "CREATE TABLE events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "event_type TEXT NOT NULL CHECK(event_type IN ('person_appeared','camera_started')),"
                    "marker TEXT UNIQUE);"
                    "INSERT INTO events VALUES(8,'person_appeared','old');"
                    "INSERT INTO events VALUES(9,'camera_started','camera');")
            backup = migrate_event_cnn_result(path)
            self.assertTrue(backup.is_file())
            with closing(sqlite3.connect(backup)) as db:
                self.assertNotIn('cnn_result', [r[1] for r in db.execute('PRAGMA table_info(events)')])
                self.assertEqual(db.execute('SELECT * FROM users').fetchall(), [(1, 'preserve')])
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT event_id,marker,cnn_result FROM events').fetchall(),
                                 [(8, 'old', None), (9, 'camera', None)])
                self.assertEqual(db.execute('SELECT * FROM users').fetchall(), [(1, 'preserve')])
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE events SET cnn_result='{}' WHERE event_id=9")
                db.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO events(event_type,marker) VALUES('person_appeared','old')")
                db.rollback()
            self.assertIsNone(migrate_event_cnn_result(path))
            self.assertEqual(len(list(path.parent.glob('*.bak'))), 1)

    def test_backup_failure_leaves_old_db_unchanged(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE events(event_id INTEGER PRIMARY KEY,event_type TEXT)')
            with patch('client_code.backend.migrations.sqlite3.connect', side_effect=[
                    sqlite3.connect(path), OSError('backup unavailable')]):
                with self.assertRaises(OSError):
                    migrate_event_cnn_result(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertNotIn('cnn_result', [r[1] for r in db.execute('PRAGMA table_info(events)')])

    def test_fresh_or_no_event_db_is_not_migrated(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fresh.db'
            self.assertIsNone(migrate_event_cnn_result(path))
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY)')
            self.assertIsNone(migrate_event_cnn_result(path))
            self.assertEqual(list(path.parent.glob('*.bak')), [])
