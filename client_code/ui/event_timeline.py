"""이벤트 타임라인 표시 전용 위젯. API 조회와 저장 작업은 담당하지 않는다."""
from datetime import datetime, timedelta, timezone

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QLabel, QLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


KST = timezone(timedelta(hours=9))
EVENT_STYLES = {
    'camera_started': ('카메라 실행', '#4ade80'),
    'camera_stopped': ('카메라 중지', '#94a3b8'),
    'person_appeared': ('등장', '#38bdf8'),
    'person_disappeared': ('사라짐', '#fb923c'),
}


def event_time(event):
    value = event['occurred_at']
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('이벤트 발생 시각에는 시간대가 필요합니다.')
    return value.astimezone(KST)


class EventCard(QFrame):
    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.setObjectName('eventCard')
        text, color = EVENT_STYLES[event['event_type']]
        self.setStyleSheet(
            f'QFrame#eventCard {{ background: #0f172a; border-radius: 6px; '
            f'border-left: 3px solid {color}; }}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        self.time_label = QLabel(event_time(event).strftime('%H:%M:%S'))
        self.time_label.setStyleSheet(f'background: transparent; color: {color}; font-size: 18px; font-weight: bold;')
        camera = f"CAM-{event['camera_id']:02d}"
        if event['event_type'].startswith('person_'):
            text = f"인물 #{event['global_person_id']} {text}"
        self.message_label = QLabel(f'{camera} · {text}')
        self.message_label.setTextFormat(Qt.PlainText)
        self.message_label.setWordWrap(True)
        self.message_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.message_label.setMinimumWidth(0)
        self.message_label.setStyleSheet('background: transparent; color: #f8fafc; font-size: 15px;')
        layout.addWidget(self.time_label)
        layout.addWidget(self.message_label)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        margins = self.layout().contentsMargins()
        width = max(1, self.contentsRect().width() - margins.left() - margins.right())
        self.message_label.setMinimumHeight(self.message_label.heightForWidth(width))


class EventTimeline(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('eventTimeline')
        self.setFixedWidth(300)
        self.setStyleSheet('QFrame#eventTimeline { background: #1e293b; border-radius: 8px; }')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        title = QLabel('이벤트 타임라인')
        title.setStyleSheet('background: transparent; font-size: 19px; font-weight: bold;')
        layout.addWidget(title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self.content = QWidget()
        self.content.setStyleSheet('background: #1e293b;')
        self.items = QVBoxLayout(self.content)
        self.items.setSizeConstraint(QLayout.SetMinAndMaxSize)
        self.items.setContentsMargins(0, 4, 4, 0)
        self.items.setSpacing(10)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.set_events([])

    def set_events(self, events):
        """저장된 이벤트 목록을 교체하고 발생 시각·ID 내림차순으로 중복 없이 표시한다."""
        unique = {event['event_id']: event for event in events}
        ordered = sorted(unique.values(), key=lambda event: (event_time(event), event['event_id']), reverse=True)
        while self.items.count():
            item = self.items.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        if not ordered:
            empty = QLabel('표시할 이벤트가 없습니다.')
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet('color: #94a3b8; font-size: 15px; padding: 24px 0;')
            self.items.addWidget(empty)
        previous_date = None
        for event in ordered:
            day = event_time(event).date()
            if day != previous_date:
                label = QLabel(day.strftime('%Y-%m-%d'))
                label.setStyleSheet('color: #cbd5e1; font-size: 14px; padding: 6px 0; border-bottom: 1px solid #334155;')
                self.items.addWidget(label)
                previous_date = day
            self.items.addWidget(EventCard(event, self.content))
        self.items.addStretch()
