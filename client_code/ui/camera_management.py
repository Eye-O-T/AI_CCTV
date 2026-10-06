"""서버 등록 정보 편집. 영상 실행과 독립적이다."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFormLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QLineEdit, QComboBox, QSpinBox, QCheckBox)
from cameras.api_client import CameraApiClient

MESSAGES = {
    'CONNECTION_ERROR': '서버 응답을 확인하지 못했습니다. 저장 요청이었다면 새로고침으로 반영 여부를 확인하세요.',
    'INVALID_RESPONSE': '서버 응답 형식을 확인할 수 없습니다. 다시 조회해 주세요.',
    'CAMERA_SOURCE_CONFLICT': '이미 등록된 입력입니다. 기존 카메라를 확인하세요.',
    'CAMERA_NOT_FOUND': '카메라가 없습니다. 목록을 새로고침해 주세요.',
    'VALIDATION_ERROR': '입력값과 종류별 필수 항목을 확인하세요.',
    'INVALID_ACCESS_TOKEN': '로그인 상태를 확인할 수 없습니다.',
}


def valid_camera(value, detail=False):
    if not isinstance(value, dict):
        return False
    if (type(value.get('camera_id')) is not int or value['camera_id'] < 1
            or not isinstance(value.get('name'), str)
            or value.get('camera_type') not in ('webcam', 'edge_node')
            or type(value.get('is_enabled')) is not bool):
        return False
    if detail:
        if value['camera_type'] == 'webcam':
            return type(value.get('webcam_index')) is int and value['webcam_index'] >= 0
        return isinstance(value.get('rtsp_url'), str) and bool(value['rtsp_url']) and (
            value.get('edge_api_base_url') is None or isinstance(value['edge_api_base_url'], str))
    return True


def camera_label(camera):
    state = '대기 중' if camera['is_enabled'] else '비활성'
    return f"CAM-{camera['camera_id']:02d} · {camera['name']}\n{state}"


class CameraManagementPage(QWidget):
    saved = pyqtSignal(int)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QLineEdit, QSpinBox, QComboBox, QListWidget {
                background-color: #0f172a; color: #f8fafc;
                border: 1px solid #475569; border-radius: 5px; padding: 7px;
                font-size: 14px;
            }
            QPushButton { background-color: #2563eb; color: white; padding: 9px;
                border-radius: 5px; font-size: 14px; }
            QPushButton:disabled { background-color: #334155; color: #94a3b8; }
            QListWidget::item { padding: 8px; }
            QListWidget::item:selected { background-color: #1d4ed8; }
            QLabel, QCheckBox { font-size: 14px; }
        """)
        self.api = CameraApiClient(session, self)
        self.api.completed.connect(self._completed)
        self.api.busy_changed.connect(self._busy)
        self.operation = self.camera_id = self.original = self.requested_id = None
        self._loading = False
        self._saved_notice = False
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)
        self.refresh_button = QPushButton('새로고침')
        self.refresh_button.clicked.connect(self.reload)
        self.new_button = QPushButton('새 카메라 등록')
        self.new_button.clicked.connect(self.new_camera)
        for widget in (self.list, self.refresh_button, self.new_button):
            left.addWidget(widget)
        layout.addLayout(left, 1)
        right = QVBoxLayout()
        self.title = QLabel('새 카메라 등록')
        self.title.setStyleSheet('font-size: 22px; font-weight: bold;')
        right.addWidget(self.title)
        self.form = QWidget()
        self.fields = QFormLayout(self.form)
        self.fields.setSpacing(14)
        self.name = QLineEdit()
        self.name.setMaxLength(100)
        self.kind = QComboBox()
        self.kind.addItem('웹캠', 'webcam')
        self.kind.addItem('엣지노드 (RTSP)', 'edge_node')
        self.index = QSpinBox()
        self.index.setRange(0, 2147483647)
        self.rtsp = QLineEdit()
        self.rtsp.setPlaceholderText('rtsp://장치주소:8554/live')
        self.edge_api = QLineEdit()
        self.edge_api.setPlaceholderText('선택: http://장치주소:8002')
        self.enabled = QCheckBox('카메라 사용')
        self.enabled.setChecked(True)
        for label, widget in (('이름', self.name), ('종류', self.kind), ('웹캠 번호', self.index),
                              ('RTSP 주소', self.rtsp), ('장치 API 기본 주소', self.edge_api), ('사용 여부', self.enabled)):
            self.fields.addRow(label, widget)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        right.addWidget(self.form)
        self.save_button = QPushButton('등록')
        self.save_button.clicked.connect(self.save)
        right.addWidget(self.save_button)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        right.addWidget(self.message)
        note = QLabel('저장은 등록 정보에만 적용됩니다. 영상 실행은 다음 단계에서 지원합니다.')
        note.setWordWrap(True)
        right.addWidget(note)
        right.addStretch()
        layout.addLayout(right, 2)
        self._kind_changed()
        self.reload()

    def _busy(self, busy):
        for widget in (self.list, self.refresh_button, self.new_button, self.form, self.save_button):
            widget.setEnabled(not busy)
        self.kind.setEnabled(not busy and self.camera_id is None)
        if not busy and self.camera_id is not None and self.original is None:
            self.form.setEnabled(False)
            self.save_button.setEnabled(False)

    def _kind_changed(self):
        webcam = self.kind.currentData() == 'webcam'
        for widget, visible in ((self.index, webcam), (self.rtsp, not webcam), (self.edge_api, not webcam)):
            widget.setVisible(visible)
            self.fields.labelForField(widget).setVisible(visible)

    def reload(self):
        self.operation = 'list'
        self.message.setText('카메라 목록 조회 중…')
        self.api.request('GET')

    def new_camera(self):
        self._saved_notice = False
        self._loading = True
        self.list.setCurrentRow(-1)
        self._loading = False
        self.camera_id = self.original = None
        self.name.clear()
        self.kind.setCurrentIndex(0)
        self.index.setValue(0)
        self.rtsp.clear()
        self.edge_api.clear()
        self.enabled.setChecked(True)
        self.title.setText('새 카메라 등록')
        self.save_button.setText('등록')
        self.message.clear()
        self._busy(False)

    def _selected(self, item, _old):
        if self._loading or item is None:
            return
        self.camera_id = self.requested_id = item.data(Qt.UserRole)
        self.original = None
        self.operation = 'detail'
        self.message.setText('카메라 정보 조회 중…')
        self.api.request('GET', f'/{self.camera_id}')

    def save(self):
        name = self.name.text().strip()
        if not name or (self.kind.currentData() == 'edge_node' and not self.rtsp.text().strip()):
            self.message.setText('이름과 종류별 필수 입력을 확인하세요.')
            return
        webcam = self.kind.currentData() == 'webcam'
        values = dict(name=name, webcam_index=self.index.value() if webcam else None,
                      rtsp_url=None if webcam else self.rtsp.text().strip(),
                      edge_api_base_url=None if webcam else (self.edge_api.text().strip() or None),
                      is_enabled=self.enabled.isChecked())
        if self.camera_id is None:
            values['camera_type'] = self.kind.currentData()
            method, path = 'POST', ''
        else:
            if self.original is None:
                return
            values = {key: value for key, value in values.items() if value != self.original.get(key)}
            if not values:
                self.message.setText('변경된 내용이 없습니다.')
                return
            method, path = 'PATCH', f'/{self.camera_id}'
        self.operation = 'save'
        self.api.request(method, path, values)

    def _completed(self, payload, error, field):
        if error:
            self.message.setText(MESSAGES.get(error, '요청에 실패했습니다. 다시 시도해 주세요.') + (f' ({field})' if field else ''))
            return
        if self.operation == 'list':
            items = payload.get('items')
            if not isinstance(items, list) or not all(valid_camera(c) for c in items):
                self.message.setText(MESSAGES['INVALID_RESPONSE'])
                return
            self._loading = True
            self.list.clear()
            selected = None
            for camera in items:
                item = QListWidgetItem(camera_label(camera))
                item.setData(Qt.UserRole, camera['camera_id'])
                if not camera['is_enabled']:
                    item.setForeground(Qt.gray)
                self.list.addItem(item)
                if camera['camera_id'] == self.camera_id:
                    selected = item
            if selected:
                self.list.setCurrentItem(selected)
            self._loading = False
            self.message.setText('등록된 카메라가 없습니다. 새 카메라를 등록하세요.' if not items else '목록을 갱신했습니다.')
            if selected:
                self._selected(selected, None)
        elif not valid_camera(payload, detail=True) or (self.operation == 'detail' and payload['camera_id'] != self.requested_id):
            self.message.setText(MESSAGES['INVALID_RESPONSE'])
            self._busy(False)
        else:
            saved = self.operation == 'save'
            self.camera_id = payload['camera_id']
            self.original = payload
            self.name.setText(payload['name'])
            self.kind.setCurrentIndex(0 if payload['camera_type'] == 'webcam' else 1)
            self.index.setValue(payload.get('webcam_index') or 0)
            self.rtsp.setText(payload.get('rtsp_url') or '')
            self.edge_api.setText(payload.get('edge_api_base_url') or '')
            self.enabled.setChecked(payload['is_enabled'])
            self.title.setText(f"CAM-{self.camera_id:02d} 정보 수정")
            self.save_button.setText('변경 저장')
            self.message.setText('저장되었습니다.' if saved or self._saved_notice else '카메라 정보를 불러왔습니다.')
            self._saved_notice = saved
            self._busy(False)
            if saved:
                self.saved.emit(self.camera_id)
                self.reload()

    def close_requests(self):
        self.api.cancel()
