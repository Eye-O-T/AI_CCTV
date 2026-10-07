"""최대 두 카메라의 동일 창 전체/개별 보기."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                            QLabel, QPushButton, QListWidget, QListWidgetItem, QDialog)
from cameras.runtime_manager import CameraRuntimeManager
from cameras.api_client import CameraApiClient, valid_camera
from ui.camera_view import CameraView
from ui.settings_window import SettingsWindow
from ui.resource_monitor_window import ResourceMonitorWindow


class CCTVMainWindow(QMainWindow):
    logout_requested = pyqtSignal()
    close_requested = pyqtSignal()
    shutdown_complete = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Intelligent CCTV Control Center')
        self.resize(1600, 900)
        self.setStyleSheet("""
            QWidget { background: #0f172a; color: #f8fafc; font-family: Arial; }
            QPushButton { background: #334155; border: none; border-radius: 5px; padding: 10px 16px; font-size: 18px; }
            QPushButton:disabled { color: #64748b; }
            QListWidget { background: #1e293b; border: none; font-size: 17px; }
            QListWidget::item { padding: 14px; }
            QListWidget::item:selected { background: #1d4ed8; }
        """)
        self.video_source = 0
        self.use_yolo = True
        self.use_vlm = False
        self.storage_root_path = self.ai_cctv_path = ''
        self.original_segment_seconds = self.clip_max_seconds = 10
        self.resource_monitor_window = None
        self.allow_close = False
        self.runtime = CameraRuntimeManager(self)
        self.camera_api = None
        self.selected_camera_id = None
        self.camera_rows = {}
        self.views = {}
        self._layout_key = None
        self.init_ui()
        self._connect_runtime()
        self._set_controls(True)

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        header = QHBoxLayout()
        title = QLabel('Intelligent CCTV Control Center')
        title.setStyleSheet('font-size: 24px; font-weight: bold;')
        header.addWidget(title)
        header.addStretch()
        for attr, text, callback in (
                ('btn_start', 'START', self.start_video), ('btn_stop', 'STOP', self.stop_video),
                ('btn_setting', '설정', self.open_settings),
                ('btn_resource_monitor', '리소스 모니터링', self.open_resource_monitor),
                ('btn_logout', '로그아웃', self.logout_requested.emit)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            setattr(self, attr, button)
            header.addWidget(button)
        self.btn_start.setStyleSheet('background: #166534;')
        self.btn_stop.setStyleSheet('background: #7f1d1d;')
        self.btn_start.setToolTip('선택한 활성 카메라를 실행합니다. 최대 두 대까지 실행할 수 있습니다.')
        layout.addLayout(header)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.cam_status = QLabel('카메라 목록을 불러오지 않았습니다.')
        self.cam_status.setTextFormat(Qt.PlainText)
        self.cam_status.setWordWrap(True)
        self.cam_status.setFixedWidth(260)
        self.cam_status.setStyleSheet('color: #facc15; font-size: 17px; padding: 10px;')
        left.addWidget(self.cam_status)
        self.camera_list = QListWidget()
        self.camera_list.setFixedWidth(280)
        self.camera_list.currentItemChanged.connect(self.select_camera)
        left.addWidget(self.camera_list)
        self.btn_camera_refresh = QPushButton('목록 새로고침')
        self.btn_camera_refresh.clicked.connect(self.reload_cameras)
        self.btn_camera_manage = QPushButton('카메라 등록 / 관리')
        self.btn_camera_manage.clicked.connect(self.open_settings)
        left.addWidget(self.btn_camera_refresh)
        left.addWidget(self.btn_camera_manage)
        body.addLayout(left)
        center = QVBoxLayout()
        self.center_title = QLabel('전체 보기 · 동시 실행 최대 2대')
        self.center_title.setTextFormat(Qt.PlainText)
        self.center_title.setStyleSheet('font-size: 22px; font-weight: bold;')
        center.addWidget(self.center_title)
        self.video_grid = QGridLayout()
        self.video_grid.setSpacing(12)
        center.addLayout(self.video_grid, 1)
        self.empty_views = []
        for _ in range(4):
            empty = QLabel('미사용')
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumSize(160, 90)
            empty.setStyleSheet('background: #1e293b; color: #64748b; font-size: 22px;')
            self.empty_views.append(empty)
        body.addLayout(center, 1)
        layout.addLayout(body, 1)
        self.storage_label = QLabel('저장 경로가 설정되지 않았습니다. 설정 → 저장 설정에서 선택하세요.')
        self.storage_label.setTextFormat(Qt.PlainText)
        self.storage_label.setWordWrap(True)
        self.storage_label.setStyleSheet('font-size: 14px; color: #cbd5e1;')
        self.statusBar().setStyleSheet('font-size: 14px;')
        layout.addWidget(self.storage_label)
        self._layout_views()

    def _connect_runtime(self):
        runtime = self.runtime
        runtime.changed.connect(self._refresh_runtime)
        runtime.detail_failed.connect(self._detail_failed)
        runtime.camera_updated.connect(self._camera_updated)
        runtime.started.connect(self._camera_started)
        runtime.monitor_ready.connect(self._show_resource_monitor)
        runtime.status_changed.connect(self._runtime_status)
        runtime.frame_ready.connect(self.update_frame)
        runtime.metrics_ready.connect(self.update_metrics)
        runtime.event_ready.connect(self.handle_event)
        runtime.loading_ready.connect(self.show_loading_screen)
        runtime.notice.connect(self._notice)
        runtime.stop_requested.connect(self._close_monitor)
        runtime.shutdown_complete.connect(self.shutdown_complete)

    def _refresh_runtime(self):
        self._layout_views()
        self._set_controls(True)

    def _view(self, camera):
        camera_id = camera['camera_id']
        if camera_id not in self.views:
            self.views[camera_id] = CameraView(camera, self)
            self.views[camera_id].hide()
        return self.views[camera_id]

    def _layout_views(self):
        key = (self.selected_camera_id, tuple(self.runtime.slots))
        if key == self._layout_key:
            return
        self._layout_key = key
        while self.video_grid.count():
            self.video_grid.takeAt(0).widget().hide()
        for i in range(2):
            self.video_grid.setRowStretch(i, 1 if self.selected_camera_id is None or i == 0 else 0)
            self.video_grid.setColumnStretch(i, 1 if self.selected_camera_id is None or i == 0 else 0)
        if self.selected_camera_id is None:
            self.center_title.setText('전체 보기 · 동시 실행 최대 2대')
            for i in range(4):
                camera_id = self.runtime.slots[i] if i < 2 else None
                camera = self.camera_rows.get(camera_id)
                widget = self._view(camera) if camera else self.empty_views[i]
                self.video_grid.addWidget(widget, i // 2, i % 2)
                widget.show()
        else:
            camera = self.camera_rows.get(self.selected_camera_id)
            if camera:
                self.center_title.setText(f"CAM-{camera['camera_id']:02d} · {camera['name']}")
                view = self._view(camera)
                self.video_grid.addWidget(view, 0, 0)
                view.show()

    def _detail_failed(self, camera_id, error):
        from ui.camera_management import MESSAGES
        self._notice(camera_id, MESSAGES.get(error, '카메라 정보를 확인할 수 없습니다. 다시 조회하세요.'))

    def _notice(self, camera_id, message):
        camera = self.camera_rows.get(camera_id)
        if camera and self.runtime.runtime_states.get(camera_id) != '중지 중':
            self._view(camera).message.setText(message)
        self.statusBar().showMessage(f'CAM-{camera_id:02d} · {message}')

    def _camera_updated(self, camera):
        self.camera_rows[camera['camera_id']] = camera
        self._view(camera).set_camera(camera)

    def bind_camera_session(self, session):
        self.camera_api = CameraApiClient(session, self)
        self.camera_api.completed.connect(self.cameras_received)
        self.camera_api.busy_changed.connect(lambda busy: self._set_controls(True))
        self.runtime.bind_session(session)
        self.reload_cameras()

    def reload_cameras(self):
        if self.camera_api is None or self.runtime.shutting_down or self.runtime.busy:
            return
        if self.camera_api.request('GET'):
            self.cam_status.setText('카메라 목록 조회 중…')

    def camera_saved(self, camera_id):
        self.selected_camera_id = camera_id
        if self.camera_api is not None:
            self.camera_api.cancel()
        self.reload_cameras()

    def cameras_received(self, payload, error, field):
        from ui.camera_management import MESSAGES
        if self.runtime.shutting_down:
            return
        if error:
            self.cam_status.setText(MESSAGES.get(error, '목록 조회 실패. 새로고침해 주세요.'))
            return
        rows = payload.get('items') if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not all(valid_camera(row) for row in rows):
            self.cam_status.setText(MESSAGES['INVALID_RESPONSE'])
            return
        previous = self.selected_camera_id
        self.camera_rows = {row['camera_id']: row for row in rows}
        self.camera_list.blockSignals(True)
        self.camera_list.clear()
        chosen = QListWidgetItem('전체 보기')
        chosen.setData(Qt.UserRole, None)
        self.camera_list.addItem(chosen)
        for row in rows:
            camera_id = row['camera_id']
            state = self.runtime.runtime_states.get(camera_id, '대기 중') if row['is_enabled'] else '비활성'
            item = QListWidgetItem(f"CAM-{camera_id:02d} · {row['name']}\n{state}")
            item.setData(Qt.UserRole, camera_id)
            if not row['is_enabled']:
                item.setForeground(Qt.gray)
            self.camera_list.addItem(item)
            view = self._view(row)
            view.set_camera(row)
            view.set_status(state)
            if camera_id == previous:
                chosen = item
        for camera_id in list(self.views):
            if camera_id not in self.camera_rows:
                view = self.views.pop(camera_id)
                self.video_grid.removeWidget(view)
                view.deleteLater()
        self.camera_list.setCurrentItem(chosen)
        self.camera_list.blockSignals(False)
        self._layout_key = None
        self.select_camera(chosen, None)
        if not rows:
            self.cam_status.setText('등록된 카메라가 없습니다. 카메라를 등록해 주세요.')

    def select_camera(self, item, _old):
        if self.runtime.shutting_down:
            return
        self.selected_camera_id = item.data(Qt.UserRole) if item else None
        self._layout_views()
        self._set_controls(True)
        self._selection_status()

    def _selection_status(self):
        camera_id = self.selected_camera_id
        if camera_id is None:
            self.cam_status.setText('전체 보기\n카메라를 선택한 뒤 START / STOP을 사용하세요.')
        else:
            state = self.runtime.runtime_states.get(camera_id, '대기 중')
            row = self.camera_rows.get(camera_id)
            if row and not row['is_enabled']:
                state = '비활성'
            self.cam_status.setText(f'CAM-{camera_id:02d} · {state}')

    def _runtime_status(self, camera, status):
        camera_id = camera['camera_id']
        self._view(camera).set_status(status)
        if status == '중지됨':
            self._view(camera).message.clear()
        if self.selected_camera_id in (None, camera_id):
            self.statusBar().showMessage(f'CAM-{camera_id:02d} · {status}')
        for i in range(self.camera_list.count()):
            item = self.camera_list.item(i)
            if item.data(Qt.UserRole) == camera_id:
                item.setText(f"CAM-{camera_id:02d} · {camera['name']}\n{status}")
        self._selection_status()

    def start_video(self):
        if self.camera_api is not None and self.camera_api.pending is not None:
            return
        self.runtime.request_detail(self.camera_rows.get(self.selected_camera_id), 'start', {
            'use_yolo': self.use_yolo, 'ai_cctv_path': self.ai_cctv_path,
            'original_segment_seconds': self.original_segment_seconds,
            'clip_max_seconds': self.clip_max_seconds,
        })

    def _camera_started(self, camera):
        self.video_source = camera['webcam_index'] if camera['camera_type'] == 'webcam' else camera['rtsp_url']
        view = self._view(camera)
        view.message.clear()
        self.statusBar().clearMessage()
        view.set_metrics({})

    def stop_video(self):
        if self.selected_camera_id is not None:
            self.runtime.stop(self.selected_camera_id)

    def begin_shutdown(self):
        self.runtime.shutting_down = True
        if self.camera_api is not None:
            self.camera_api.cancel()
        for dialog in self.findChildren(QDialog):
            dialog.close()
        self.runtime.begin_shutdown()

    def _set_controls(self, enabled):
        available = enabled and not self.runtime.shutting_down
        api_busy = self.camera_api is not None and self.camera_api.pending is not None
        idle = available and not self.runtime.busy and not api_busy
        row = self.camera_rows.get(self.selected_camera_id)
        run = self.runtime.runs.get(self.selected_camera_id)
        self.btn_start.setEnabled(bool(available and not api_busy and self.runtime.can_start(row)))
        self.btn_stop.setEnabled(bool(available and run and not run.stopping))
        self.btn_setting.setEnabled(idle)
        self.btn_camera_manage.setEnabled(idle)
        self.camera_list.setEnabled(available)
        self.btn_camera_refresh.setEnabled(bool(idle and self.camera_api))
        monitor_ok = self.runtime.monitor_camera_id in (None, self.selected_camera_id)
        self.btn_resource_monitor.setEnabled(bool(available and row and row['is_enabled'] and monitor_ok
                                                 and (not run or not run.pending and not run.stopping)))
        self.btn_logout.setEnabled(available)

    def open_settings(self):
        if self.runtime.busy or self.runtime.shutting_down:
            return
        dialog = SettingsWindow(self, video_source=self.video_source, use_yolo=self.use_yolo,
                                use_vlm=False, storage_root_path=self.storage_root_path,
                                ai_cctv_path=self.ai_cctv_path,
                                original_segment_seconds=self.original_segment_seconds,
                                clip_max_seconds=self.clip_max_seconds,
                                session=self.camera_api.session if self.camera_api else None)
        if dialog.camera_page is not None:
            dialog.camera_page.saved.connect(self.camera_saved)
        if dialog.exec_():
            self.use_yolo = dialog.use_yolo
            self.storage_root_path = dialog.storage_root_path
            self.ai_cctv_path = dialog.ai_cctv_path
            self.original_segment_seconds = dialog.original_segment_seconds
            self.clip_max_seconds = dialog.clip_max_seconds
            self.storage_label.setText('저장 경로: ' + (self.ai_cctv_path or '미설정'))
            self.statusBar().showMessage('프로그램 설정을 적용했습니다.')
        dialog.deleteLater()

    def open_resource_monitor(self):
        if self.runtime.shutting_down or self.selected_camera_id is None:
            return
        if self.resource_monitor_window is not None:
            if self.runtime.monitor_camera_id == self.selected_camera_id:
                self.resource_monitor_window.raise_()
            return
        self.runtime.request_detail(self.camera_rows.get(self.selected_camera_id), 'monitor')

    def _show_resource_monitor(self, camera):
        self.resource_monitor_window = ResourceMonitorWindow(
            self, storage_path=self.ai_cctv_path or self.storage_root_path,
            resource_server_url=camera.get('edge_api_base_url'))
        self.resource_monitor_window.setWindowTitle(f"리소스 모니터링 · CAM-{camera['camera_id']:02d} · {camera['name']}")
        self.resource_monitor_window.finished.connect(self.handle_resource_monitor_closed)
        self.resource_monitor_window.show()
        self._set_controls(True)

    def _close_monitor(self, camera_id):
        if self.resource_monitor_window is not None and self.runtime.monitor_camera_id == camera_id:
            self.resource_monitor_window.close()
        elif self.resource_monitor_window is None and self.runtime.monitor_camera_id == camera_id:
            self.runtime.monitor_closed()

    def handle_resource_monitor_closed(self):
        if self.resource_monitor_window is not None:
            self.resource_monitor_window.deleteLater()
        self.resource_monitor_window = None
        self.runtime.monitor_closed()

    def update_frame(self, camera_id, frame):
        view = self.views.get(camera_id)
        if view:
            view.set_frame(frame)

    def update_metrics(self, camera_id, data):
        view = self.views.get(camera_id)
        if view:
            view.set_metrics(data)

    def show_loading_screen(self, camera_id, message):
        view = self.views.get(camera_id)
        if view:
            view.video.setText(message)

    def handle_event(self, camera_id, event):
        messages = {'network_failure': '네트워크 장애 / 재연결 중',
                    'network_recovered': '장애 복구 영상 저장 완료', 'error': '영상·AI 처리 오류'}
        if event.get('type') in messages:
            self._notice(camera_id, event.get('message', messages[event['type']]))
        # 인물 이벤트는 표시하지 않는다. Worker의 인물·클립 정리는 계속된다.

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.close_requested.emit()
