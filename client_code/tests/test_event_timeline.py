"""타임라인 표시만 검증한다. 서버·카메라·운영 DB를 사용하지 않는다."""
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtWidgets import QApplication, QLabel
from ui.event_timeline import EventCard, EventTimeline

APP = QApplication.instance() or QApplication([])


class EventTimelineTests(unittest.TestCase):
    def setUp(self):
        self.panel = EventTimeline()
        self.panel.resize(300, 420)
        self.panel.show()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        APP.processEvents()

    def cards(self):
        return [self.panel.items.itemAt(i).widget() for i in range(self.panel.items.count())
                if isinstance(self.panel.items.itemAt(i).widget(), EventCard)]

    def test_empty_and_replace(self):
        self.assertIn('표시할 이벤트가 없습니다.', [label.text() for label in self.panel.findChildren(QLabel)])
        self.panel.set_events([dict(event_id=1, event_type='camera_started', camera_id=1,
                                    occurred_at=datetime.now(timezone.utc))])
        self.assertEqual(len(self.cards()), 1)
        self.panel.set_events([])
        self.assertEqual(self.cards(), [])

    def test_kst_date_order_duplicate_and_global_id(self):
        events = [dict(event_id=i, event_type=kind, camera_id=12, global_person_id=987,
                       occurred_at=stamp) for i, kind, stamp in (
                           (1, 'camera_started', '2026-11-19T14:59:59Z'),
                           (2, 'person_appeared', '2026-11-19T15:00:00Z'),
                           (3, 'person_disappeared', '2026-11-19T15:00:00Z'),
                           (4, 'camera_stopped', '2026-11-19T15:00:01Z'))]
        self.panel.set_events(events + [events[0]])
        cards = self.cards()
        self.assertEqual([card.message_label.text() for card in cards], [
            'CAM-12 · 카메라 중지', 'CAM-12 · 인물 #987 사라짐',
            'CAM-12 · 인물 #987 등장', 'CAM-12 · 카메라 실행'])
        self.assertEqual([card.time_label.text() for card in cards], ['00:00:01', '00:00:00', '00:00:00', '23:59:59'])
        dates = [self.panel.items.itemAt(i).widget().text() for i in range(self.panel.items.count())
                 if isinstance(self.panel.items.itemAt(i).widget(), QLabel)]
        self.assertEqual(dates, ['2026-11-20', '2026-11-19'])

    def test_many_cards_scroll_and_long_text_fits(self):
        self.panel.set_events([dict(event_id=i, event_type='person_appeared', camera_id=9223372036854775807,
                                   global_person_id=9223372036854775807,
                                   occurred_at='2026-11-20T01:00:00Z') for i in range(1, 61)])
        APP.processEvents()
        self.assertGreater(self.panel.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.panel.scroll.horizontalScrollBar().maximum(), 0)
        for card in self.cards():
            label = card.message_label
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))


if __name__ == '__main__':
    unittest.main()
