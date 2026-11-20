"""저장 성공→타임라인 자동 반영, 조회 경합·스크롤·정상 종료 검증."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4

import httpx
import test_event_history as fixture
from events.coordinator import EventCoordinator
from ui.event_timeline import EventCard

APP, wait_for = fixture.APP, fixture.wait_for


class EventLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.EventHistoryTests.setUpClass()
        cls.url = fixture.EventHistoryTests.url
        with httpx.Client(base_url=cls.url) as client:
            token = client.post('/api/v1/auth/login', json=dict(username='admin', password='test-pass')).json()['access_token']
            client.headers['Authorization'] = 'Bearer ' + token
            result = client.post('/api/v1/global-persons', json=dict(request_id=str(uuid4()),
                feature_vector=[1.0] + [0.0] * 511, observed_at='2026-11-20T00:00:00Z'))
            result.raise_for_status()
            cls.global_id = result.json()['global_person_id']

    @classmethod
    def tearDownClass(cls):
        fixture.EventHistoryTests.tearDownClass()

    def setUp(self):
        self.fixture = fixture.EventHistoryTests()
        self.fixture.setUp()
        self.session, self.panel, self.history = self.fixture.session, self.fixture.panel, self.fixture.history
        self.coordinator = EventCoordinator(self.session)
        self.coordinator.event_saved.connect(self.history.receive_saved)
        self.saved = []
        self.coordinator.event_saved.connect(self.saved.append)
        wait_for(lambda: self.coordinator.ready)
        self.execution = uuid4().hex
        self.first = datetime(2026, 11, 20, 3, tzinfo=timezone.utc)

    def tearDown(self):
        self.coordinator.begin_shutdown()
        wait_for(lambda: self.coordinator.finished)
        self.coordinator.store.join(4)
        self.coordinator.deleteLater()
        self.fixture.tearDown()

    def flush(self):
        wait_for(lambda: not self.coordinator._pending)
        for _ in range(3):
            APP.processEvents()

    def camera(self, kind='camera_started', offset=0):
        self.coordinator.camera_event(kind, 1, self.execution, self.first + timedelta(seconds=offset))

    def snapshot(self, **changes):
        values = dict(camera_id=1, execution_id=self.execution, tracking_generation=0, local_person_id=1,
            global_person_id=self.global_id, crop_task_id=uuid4(), first_seen_utc=self.first,
            last_seen_utc=self.first + timedelta(seconds=5), revision=1, finished_at=None,
            finish_reason=None, crop_path=None, clip_path=None, trajectory_path=None)
        values.update(changes)
        return SimpleNamespace(**values)

    def cards(self):
        return [self.panel.items.itemAt(i).widget() for i in range(self.panel.items.count())
                if isinstance(self.panel.items.itemAt(i).widget(), EventCard)]

    def test_four_saved_types_match_api_and_patch_never_adds_card(self):
        self.fixture.load()
        self.camera()
        self.flush()
        initial = self.snapshot()
        self.coordinator.person_update(initial)
        self.flush()
        appeared = next(event for event in self.saved if event['event_type'] == 'person_appeared')
        path = Path(self.coordinator.root) / f'이벤트/person_{self.global_id}/2026-11-20/camera_1/12시00분00초/crop.jpg'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'complete-test-file')
        initial.revision = 2
        initial.crop_path = path
        self.coordinator.person_update(initial)
        self.flush()
        self.assertEqual(len(self.saved), 2)
        initial.revision = 3
        initial.finished_at = initial.last_seen_utc
        initial.finish_reason = 'natural'
        self.coordinator.person_update(initial)
        self.flush()
        self.camera('camera_stopped', 10)
        self.flush()
        self.assertEqual({event['event_type'] for event in self.saved},
                         {'camera_started', 'camera_stopped', 'person_appeared', 'person_disappeared'})
        self.assertEqual(len(self.saved), 4)
        with httpx.Client(base_url=self.url, headers={'Authorization': 'Bearer ' + self.session.access_token}) as client:
            for event in self.saved:
                row = client.get(f"/api/v1/events/{event['event_id']}").json()
                for field in ('event_id', 'event_type', 'camera_id', 'global_person_id', 'occurred_at'):
                    self.assertEqual(self.history.events[event['event_id']][field], row[field])
            self.assertIsNotNone(client.get(f"/api/v1/events/{appeared['event_id']}").json()['crop_path'])
        self.assertTrue(any(f'인물 #{self.global_id}' in card.message_label.text() for card in self.cards()))

    def test_initial_and_older_query_overlap_saved_results_without_losing_cursor(self):
        fixture.EventHistoryTests.mode = 'snapshot'
        self.history.load_more()
        wait_for(lambda: fixture.EventHistoryTests.snapshot_ready)
        self.camera()
        self.flush()
        saved_id = self.saved[-1]['event_id']
        wait_for(lambda: not self.history.pending)
        self.assertIn(saved_id, self.history.events)
        self.assertEqual(len(self.history.events), 51)
        self.assertEqual(len([card for card in self.cards() if card.event['event_id'] == saved_id]), 1)
        cursor = self.history.cursor
        fixture.EventHistoryTests.mode = 'slow'
        self.history.load_more()
        self.execution = uuid4().hex
        # 이벤트 ID가 커도 발생 시각이 과거라면 아래쪽에 삽입한다.
        self.first = datetime(2026, 11, 18, tzinfo=timezone.utc)
        self.camera()
        self.flush()
        old_event = self.saved[-1]
        self.assertEqual(self.history.cursor, cursor)
        wait_for(lambda: not self.history.pending)
        self.assertIn(old_event['event_id'], self.history.events)
        self.assertEqual(len(self.cards()), len(self.history.events))
        self.assertEqual(self.cards()[-1].event['event_id'], old_event['event_id'])
        self.assertEqual(int(fixture.EventHistoryTests.requests[-1]['before_event_id']), cursor[1])

    def test_scroll_anchor_and_top_follow_late_event_sorted_by_occurrence(self):
        self.fixture.load()
        for _ in range(3): APP.processEvents()
        bar = self.panel.scroll.verticalScrollBar()
        bar.setValue(430)
        anchor = next(card for card in self.cards() if card.geometry().bottom() >= bar.value())
        offset = anchor.y() - bar.value()
        anchor_id = anchor.event['event_id']
        self.camera()
        self.flush()
        self.assertIs(self.panel.cards[anchor_id], anchor)
        self.assertEqual(anchor.y() - bar.value(), offset)
        # 같은 GUI 처리 주기에 여러 번 도착해도 읽던 위치를 유지한다.
        for i in range(3):
            row = dict(self.saved[-1], event_id=9000+i, occurred_at=f'2026-11-21T00:00:0{i}Z')
            self.history.receive_saved(row)
        for _ in range(3): APP.processEvents()
        self.assertEqual(anchor.y() - bar.value(), offset)
        bar.setValue(0)
        self.execution = uuid4().hex
        self.first += timedelta(days=3)
        self.camera()
        self.flush()
        self.assertEqual(bar.value(), 0)
        self.assertEqual(self.cards()[0].event['event_id'], self.saved[-1]['event_id'])

    def test_failed_save_invalid_response_and_replayed_results_do_not_add_cards(self):
        self.fixture.load()
        before = set(self.history.events)
        self.coordinator.camera_event('camera_started', 99999, self.execution, self.first)
        self.flush()
        self.assertEqual(self.saved, [])
        self.assertEqual(set(self.history.events), before)
        self.history.receive_saved({'event_id': True})
        self.assertEqual(set(self.history.events), before)
        self.camera()
        self.flush()
        self.history.receive_saved(self.saved[-1])
        self.assertEqual(len(self.history.events), len(before) + 1)

    def test_final_save_during_read_shutdown_then_signed_out_rejects_old_results(self):
        self.camera()
        self.flush()
        self.history.stop()
        self.camera('camera_stopped', 10)
        self.coordinator.begin_shutdown()
        wait_for(lambda: self.coordinator.finished)
        self.assertEqual(len(self.saved), 2)
        self.assertIn(self.saved[-1]['event_id'], self.history.events)
        self.session.logout()
        wait_for(lambda: self.session.access_token is None)
        count = len(self.history.events)
        self.history.receive_saved(dict(self.saved[-1], event_id=999999))
        self.assertEqual(len(self.history.events), count)

    def test_main_window_live_connection_and_final_queue_drain(self):
        from ui.gui import CCTVMainWindow
        window = CCTVMainWindow()
        window.show()
        try:
            window.bind_camera_session(self.session)
            store = window.runtime.event_store
            wait_for(lambda: store.ready and not window.event_history.pending)
            rows = []
            store.event_saved.connect(rows.append)
            store.camera_event('camera_started', 1, self.execution, self.first)
            wait_for(lambda: not store._pending)
            observation = self.snapshot()
            store.person_update(observation)
            wait_for(lambda: not store._pending)
            observation.revision = 2
            observation.finished_at = observation.last_seen_utc
            observation.finish_reason = 'natural'
            store.person_update(observation)
            wait_for(lambda: not store._pending)
            store.camera_event('camera_stopped', 1, self.execution, self.first + timedelta(seconds=10))
            window.begin_shutdown()
            wait_for(lambda: store.finished)
            self.assertEqual(len(rows), 4)
            for row in rows:
                self.assertIn(row['event_id'], window.event_history.events)
                self.assertIn(row['event_id'], window.event_timeline.cards)
            self.assertTrue(self.session.access_token)
            self.assertEqual(window.video_grid.count(), 4)
            store.store.join(4)
        finally:
            if window.runtime.event_store is not None:
                window.runtime.event_store.store.abort()
                window.runtime.event_store.store.join(4)
            window.allow_close = True
            window.close()
            window.deleteLater()


if __name__ == '__main__':
    unittest.main()
