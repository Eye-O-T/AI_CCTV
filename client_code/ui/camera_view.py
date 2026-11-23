"""같은 카메라 위젯을 전체/개별 보기에서 재사용한다."""
import cv2
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout, QSizePolicy


class CameraView(QFrame):
    def __init__(self, camera, parent=None):
        super().__init__(parent)
        self.camera_id = camera['camera_id']
        self.pixmap = None
        self.setStyleSheet('QFrame { background: #1e293b; border-radius: 8px; }')
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setTextFormat(Qt.PlainText)
        self.title.setStyleSheet('font-size: 18px; font-weight: bold;')
        self.status = QLabel('대기 중')
        self.status.setStyleSheet('color: #facc15; font-size: 15px;')
        self.video = QLabel('카메라를 선택하고 START를 누르세요.')
        self.video.installEventFilter(self)
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setWordWrap(True)
        self.video.setMinimumSize(160, 90)
        self.video.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.video.setStyleSheet('background: #0f172a; color: #94a3b8; font-size: 17px;')
        self.current = QLabel('현재 탐지 인원: 0')
        self.current.setStyleSheet('font-size: 15px;')
        self.message = QLabel('')
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setStyleSheet('color: #facc15; font-size: 14px;')
        for widget in (self.title, self.status, self.video, self.current, self.message):
            layout.addWidget(widget, 1 if widget is self.video else 0)
        self.set_camera(camera)

    def set_camera(self, camera):
        self.title.setText(f"CAM-{camera['camera_id']:02d} · {camera['name']}")

    def set_status(self, status):
        self.status.setText(status)
        if status != 'LIVE':
            self.pixmap = None
            self.video.clear()
            self.video.setText(status)
            self.current.setText('현재 탐지 인원: —')

    def set_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        # 수신 버퍼와 수명을 분리하고 GUI 스레드에서만 QPixmap을 만든다.
        self.pixmap = QPixmap.fromImage(QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy())
        self._fit()

    def set_metrics(self, data):
        self.current.setText(f"현재 탐지 인원: {data.get('current_objects', 0)}")

    def _fit(self):
        if self.pixmap is not None:
            self.video.setPixmap(self.pixmap.scaled(self.video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def eventFilter(self, watched, event):
        if watched is self.video and event.type() == QEvent.Resize:
            self._fit()
        return super().eventFilter(watched, event)
