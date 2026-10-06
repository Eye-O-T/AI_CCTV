import cv2
from datetime import datetime
from urllib.parse import urlparse

from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QDialog,
    QListWidget,
    QListWidgetItem,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
import time
from PyQt5.QtGui import QImage, QPixmap
from ui.settings_window import SettingsWindow
from ui.resource_monitor_window import ResourceMonitorWindow


class CCTVMainWindow(QMainWindow):
    logout_requested = pyqtSignal()
    close_requested = pyqtSignal()
    shutdown_complete = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Intelligent CCTV Control Center")
        self.setGeometry(100, 100, 1600, 900)
        self.setStyleSheet(
            "background-color: #0f172a; color: #f8fafc; font-family: Arial;"
        )

        self.worker = None
        self.appear_count = 0
        self.disappear_count = 0
        self.video_source = 0
        self.use_yolo = True
        self.use_vlm = True
        self.storage_root_path = ""
        self.ai_cctv_path = ""
        self.original_segment_seconds = 10
        self.clip_max_seconds = 10
        self.resource_monitor_window = None
        self.allow_close = False
        self._stopping = False
        self._shutting_down = False
        self._stop_started = 0
        self._stop_timer = QTimer(self)
        self._stop_timer.setInterval(100)
        self._stop_timer.timeout.connect(self._poll_stop)

        self.camera_api = None
        self.selected_camera_id = None
        self.camera_rows = {}
        self.init_ui()
        self.btn_start.setEnabled(False)
        self.btn_start.setToolTip("카메라 영상 실행 연결은 다음 단계에서 지원합니다.")
        self.btn_resource_monitor.setEnabled(False)
        self.btn_resource_monitor.setToolTip("선택 카메라의 장치 연결은 다음 단계에서 지원합니다.")

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        header_layout = QHBoxLayout()

        title_label = QLabel("Intelligent CCTV Control Center")
        title_label.setStyleSheet("font-size: 28px; font-weight: bold;")

        self.btn_start = QPushButton("START")
        self.btn_start.setStyleSheet(
            "background-color: #166534; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-size: 22px; font-weight: bold;"
        )
        self.btn_start.clicked.connect(self.start_video)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setStyleSheet(
            "background-color: #7f1d1d; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-size: 22px; font-weight: bold;"
        )
        self.btn_stop.clicked.connect(self.stop_video)
        self.btn_setting = QPushButton("설정")
        self.btn_setting.setStyleSheet(
            "background-color: #334155; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-size: 22px; font-weight: bold;"
        )
        self.btn_setting.clicked.connect(self.open_settings)

        self.btn_resource_monitor = QPushButton("리소스 모니터링")
        self.btn_resource_monitor.setStyleSheet(
            "background-color: #0e7490; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-size: 22px; font-weight: bold;"
        )
        self.btn_resource_monitor.clicked.connect(self.open_resource_monitor)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.btn_start)
        header_layout.addWidget(self.btn_stop)
        header_layout.addWidget(self.btn_setting)
        header_layout.addWidget(self.btn_resource_monitor)
        self.btn_logout = QPushButton("로그아웃")
        self.btn_logout.setStyleSheet(
            "background-color: #334155; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-size: 22px; font-weight: bold;"
        )
        self.btn_logout.clicked.connect(self.logout_requested)
        header_layout.addWidget(self.btn_logout)

        main_layout.addLayout(header_layout)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(20)
        main_layout.addLayout(body_layout)

        left_panel = QFrame()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")

        left_layout = QVBoxLayout(left_panel)

        cam_label = QLabel("카메라\nRTSP / LAN / USB 입력 상태")
        cam_label.setStyleSheet("color: #94a3b8; font-size: 17px;")
        left_layout.addWidget(cam_label)

        self.cam_status = QLabel("카메라 목록을 불러오지 않았습니다.")
        self.cam_status.setWordWrap(True)
        self.cam_status.setTextFormat(Qt.PlainText)
        self.cam_status.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #3b82f6; "
            "border-radius: 5px; padding: 15px; color: #facc15; font-size: 22px;"
        )
        left_layout.addWidget(self.cam_status)
        self.camera_list = QListWidget()
        self.camera_list.currentItemChanged.connect(self.select_camera)
        left_layout.addWidget(self.camera_list)
        self.btn_camera_refresh = QPushButton("목록 새로고침")
        self.btn_camera_refresh.clicked.connect(self.reload_cameras)
        left_layout.addWidget(self.btn_camera_refresh)
        self.btn_camera_manage = QPushButton("카메라 등록 / 관리")
        self.btn_camera_manage.clicked.connect(self.open_settings)
        left_layout.addWidget(self.btn_camera_manage)

        body_layout.addWidget(left_panel)

        center_panel = QFrame()
        center_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")
        center_layout = QVBoxLayout(center_panel)

        center_title = QLabel("카메라를 선택하세요")
        self.center_title = center_title
        center_title.setTextFormat(Qt.PlainText)
        center_title.setStyleSheet("font-size: 22px; font-weight: bold;")
        center_layout.addWidget(center_title)

        self.video_label = QLabel("카메라 관리 연결 단계입니다.\n영상 실행은 다음 단계에서 지원합니다.")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #0f172a; border-radius: 5px; "
            "font-size: 28px; color: #334155; font-weight: bold;"
        )
        self.video_label.setMinimumSize(800, 450)
        center_layout.addWidget(self.video_label, stretch=1)

        metrics_layout = QHBoxLayout()

        self.metric_current = self.create_metric_box("0", "현재 객체")
        self.metric_total = self.create_metric_box("0", "누적 추적")
        self.metric_appear = self.create_metric_box("0", "출현")
        self.metric_disappear = self.create_metric_box("0", "사라짐")

        metrics_layout.addWidget(self.metric_current["box"])
        metrics_layout.addWidget(self.metric_total["box"])
        metrics_layout.addWidget(self.metric_appear["box"])
        metrics_layout.addWidget(self.metric_disappear["box"])

        center_layout.addLayout(metrics_layout)

        body_layout.addWidget(center_panel, stretch=1)

        right_panel = QFrame()
        right_panel.setFixedWidth(350)
        right_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")

        right_layout = QVBoxLayout(right_panel)

        event_label = QLabel("이벤트 타임라인\n출현 · 이동 · 사라짐 중심")
        event_label.setStyleSheet("color: #94a3b8; font-size: 17px;")
        right_layout.addWidget(event_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")

        scroll_widget = QWidget()
        self.event_list = QVBoxLayout(scroll_widget)
        self.event_list.setAlignment(Qt.AlignTop)

        scroll.setWidget(scroll_widget)

        right_layout.addWidget(scroll)
        right_layout.addStretch()

        self.storage_label = QLabel(
            "저장 경로\n"
            "저장 경로가 설정되지 않았습니다.\n\n"
            "설정 - 저장 설정에서 위치를 선택하세요."
        )
        self.storage_label.setWordWrap(True)
        self.storage_label.setStyleSheet("font-size: 17px; font-weight: bold;")
        right_layout.addWidget(self.storage_label)

        body_layout.addWidget(right_panel)

    def create_metric_box(self, value, label):
        box = QFrame()
        box.setStyleSheet("background-color: #0f172a; border-radius: 5px;")

        layout = QVBoxLayout(box)

        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 34px; font-weight: bold;")

        text_label = QLabel(label)
        text_label.setStyleSheet("color: #cbd5e1; font-size: 22px;")

        layout.addWidget(value_label)
        layout.addWidget(text_label)

        return {
            "box": box,
            "value": value_label,
            "label": text_label
        }

    def bind_camera_session(self, session):
        from cameras.api_client import CameraApiClient
        self.camera_api = CameraApiClient(session, self)
        self.camera_api.completed.connect(self.cameras_received)
        self.camera_api.busy_changed.connect(lambda busy: self.btn_camera_refresh.setEnabled(not busy and not self._shutting_down))
        self.reload_cameras()

    def reload_cameras(self):
        if self.camera_api is None or self._shutting_down:
            return
        if self.camera_api.request("GET"):
            self.cam_status.setText("카메라 목록 조회 중…")

    def camera_saved(self, camera_id):
        self.selected_camera_id = camera_id
        if self.camera_api is not None:
            self.camera_api.cancel()
        self.reload_cameras()

    def cameras_received(self, payload, error, field):
        from ui.camera_management import valid_camera, camera_label, MESSAGES
        if self._shutting_down:
            return
        if error:
            self.cam_status.setText(MESSAGES.get(error, "목록 조회 실패. 새로고침해 주세요."))
            return
        rows = payload.get("items")
        if not isinstance(rows, list) or not all(valid_camera(row) for row in rows):
            self.cam_status.setText(MESSAGES["INVALID_RESPONSE"])
            return
        previous = self.selected_camera_id
        self.camera_list.blockSignals(True)
        self.camera_list.clear()
        self.camera_rows = {row["camera_id"]: row for row in rows}
        chosen = None
        for row in rows:
            item = QListWidgetItem(camera_label(row))
            item.setData(Qt.UserRole, row["camera_id"])
            if not row["is_enabled"]:
                item.setForeground(Qt.gray)
            self.camera_list.addItem(item)
            if row["camera_id"] == previous:
                chosen = item
        if chosen:
            self.camera_list.setCurrentItem(chosen)
        self.camera_list.blockSignals(False)
        self.select_camera(chosen, None)
        self.cam_status.setText("등록된 카메라가 없습니다. 카메라를 등록해 주세요." if not rows else "카메라를 선택하세요. 영상 실행은 준비 중입니다.")

    def select_camera(self, item, _old):
        self.selected_camera_id = item.data(Qt.UserRole) if item else None
        row = self.camera_rows.get(self.selected_camera_id)
        self.center_title.setText(f"CAM-{row['camera_id']:02d} · {row['name']}" if row else "카메라를 선택하세요")
        self.video_label.setText("비활성 카메라입니다." if row and not row['is_enabled'] else "영상 실행은 다음 단계에서 지원합니다.")

    def start_video(self):
        self.statusBar().showMessage("카메라 영상 실행은 다음 단계에서 지원합니다.")
        return

    def _start_legacy_video(self):
        if self.worker is not None or self._stopping or self._shutting_down:
            return

        from workers.video_worker import VideoWorker

        source = self.video_source

        self.worker = VideoWorker(
            source=source,
            use_yolo=self.use_yolo,
            use_vlm=self.use_vlm,
            ai_cctv_path=self.ai_cctv_path,
            original_segment_seconds=self.original_segment_seconds,
            clip_max_seconds=self.clip_max_seconds
        )
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.metrics_ready.connect(self.update_metrics)
        self.worker.event_ready.connect(self.add_event)
        self.worker.loading_ready.connect(self.show_loading_screen)
        self.worker.finished.connect(self.handle_worker_finished)
        self.show_loading_screen("시스템 시작 중...")
        self.worker.start()

        self.cam_status.setText("● CAM-01 · 로딩 중")
        self.cam_status.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #facc15; "
            "border-radius: 5px; padding: 15px; color: #facc15; font-size: 22px;"
        )

    def stop_video(self):
        if self._stopping:
            return
        self._stopping = True
        self._stop_started = time.monotonic()
        self._set_controls(False)
        if self.worker is not None:
            self.worker.request_stop()
        self.statusBar().showMessage("영상 수신·녹화·분석을 정리하고 있습니다…")
        self._stop_timer.start()

    def begin_shutdown(self):
        self._shutting_down = True
        if self.camera_api is not None:
            self.camera_api.cancel()
        for dialog in self.findChildren(QDialog):
            dialog.close()
        self.stop_video()

    def _set_controls(self, enabled):
        for button in (self.btn_start, self.btn_stop, self.btn_setting, self.btn_resource_monitor, self.btn_logout):
            button.setEnabled(enabled)
        self.btn_start.setEnabled(False)
        self.btn_resource_monitor.setEnabled(False)
        self.btn_camera_manage.setEnabled(enabled)
        self.btn_camera_refresh.setEnabled(enabled)
        self.camera_list.setEnabled(enabled)

    def _poll_stop(self):
        video_done = self.worker is None or not self.worker.isRunning()
        monitor_done = not self._shutting_down or self.resource_monitor_window is None
        if not video_done or not monitor_done:
            if time.monotonic() - self._stop_started > 30:
                self.statusBar().showMessage("정리에 시간이 걸리고 있습니다. 분석·수신·복구 작업 종료를 기다리는 중입니다.")
            return
        if self.worker is not None:
            if not self.worker.cleanup_ok:
                self.statusBar().showMessage("작업 정리에 실패했습니다. 안전한 종료가 확인되지 않아 화면 전환을 중단했습니다.")
                self._stop_timer.stop()
                return
            self.worker.deleteLater()
            self.worker = None
        self._stop_timer.stop()
        self._stopping = False
        self.cam_status.setText("영상 작업 중지됨")
        self.cam_status.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #ef4444; "
            "border-radius: 5px; padding: 15px; color: #ef4444; font-size: 22px;"
        )
        self.show_idle_screen()
        self.statusBar().clearMessage()
        if self._shutting_down:
            self.shutdown_complete.emit()
        else:
            self._set_controls(True)

    def open_settings(self):
        dialog = SettingsWindow(
            self,
            video_source=self.video_source,
            use_yolo=self.use_yolo,
            use_vlm=self.use_vlm,
            storage_root_path=self.storage_root_path,
            ai_cctv_path=self.ai_cctv_path,
            original_segment_seconds=self.original_segment_seconds,
            clip_max_seconds=self.clip_max_seconds,
            session=self.camera_api.session if self.camera_api is not None else None
        )
        if dialog.camera_page is not None:
            dialog.camera_page.saved.connect(self.camera_saved)
        if dialog.exec_():
            self.use_yolo = dialog.use_yolo
            self.use_vlm = dialog.use_vlm

            self.storage_root_path = dialog.storage_root_path
            self.ai_cctv_path = dialog.ai_cctv_path
            self.original_segment_seconds = dialog.original_segment_seconds
            self.clip_max_seconds = dialog.clip_max_seconds

            self.statusBar().showMessage("프로그램 설정을 적용했습니다.")

            if self.ai_cctv_path:
                self.storage_label.setText(
                    "저장 경로\n"
                    f"{self.ai_cctv_path}\n\n"
                    "하위 폴더\n"
                    "원본 녹화본\n"
                    "이벤트 CLIP(YOLO 사용 시)"
                )
            else:
                self.storage_label.setText(
                    "저장 경로\n"
                    "저장 경로가 설정되지 않았습니다.\n\n"
                    "설정 → 저장 설정에서 위치를 선택하세요."
                )

        dialog.deleteLater()

    def _build_resource_monitor_url(self, source):
        parsed = urlparse(str(source))
        host = parsed.hostname
        if not host:
            return None
        return f"http://{host}:8002"

    def open_resource_monitor(self):
        if self.resource_monitor_window is None:
            self.resource_monitor_window = ResourceMonitorWindow(
                self,
                storage_path=self.ai_cctv_path or self.storage_root_path,
                resource_server_url=self._build_resource_monitor_url(
                    self.video_source
                )
            )
            self.resource_monitor_window.finished.connect(
                self.handle_resource_monitor_closed
            )

        self.resource_monitor_window.show()
        self.resource_monitor_window.raise_()
        self.resource_monitor_window.activateWindow()

    def handle_resource_monitor_closed(self):
        if self.resource_monitor_window is not None:
            self.resource_monitor_window.deleteLater()
        self.resource_monitor_window = None

    def update_frame(self, frame):
        if self.cam_status.text() != "● CAM-01 · LIVE":
            self.cam_status.setText("● CAM-01 · LIVE")
            self.cam_status.setStyleSheet(
                "background-color: #0f172a; border: 1px solid #22c55e; "
                "border-radius: 5px; padding: 15px; color: #22c55e; font-size: 22px;"
            )

        self.video_label.setStyleSheet(
            "background-color: #0f172a; border-radius: 5px; "
            "font-size: 28px; color: #334155; font-weight: bold;"
        )

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        h, w, ch = rgb_frame.shape
        bytes_per_line = ch * w

        qt_img = QImage(
            rgb_frame.data,
            w,
            h,
            bytes_per_line,
            QImage.Format_RGB888
        )

        pixmap = QPixmap.fromImage(qt_img)
        scaled_pixmap = pixmap.scaled(
            self.video_label.width(),
            self.video_label.height(),
            Qt.KeepAspectRatio
        )

        self.video_label.setPixmap(scaled_pixmap)

    def set_camera_status(self, text, border_color, text_color):
        self.cam_status.setText(text)
        self.cam_status.setStyleSheet(
            f"background-color: #0f172a; border: 1px solid {border_color}; "
            f"border-radius: 5px; padding: 15px; color: {text_color}; font-size: 22px;"
        )

    def show_loading_screen(self, message):
        self.video_label.clear()
        self.video_label.setText(f"{message}\n잠시만 기다려 주세요.")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #334155; "
            "border-radius: 5px; font-size: 28px; color: #facc15; "
            "font-weight: bold;"
        )

    def show_idle_screen(self):
        self.video_label.clear()
        self.video_label.setText("LIVE VIDEO SURFACE")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #0f172a; border-radius: 5px; "
            "font-size: 28px; color: #334155; font-weight: bold;"
        )

    def show_network_failure_screen(self):
        self.video_label.clear()
        self.video_label.setText(
            "네트워크 연결 장애\n네트워크 연결 상태를 확인하세요."
        )
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #000000; border: 1px solid #ef4444; "
            "border-radius: 5px; font-size: 28px; color: #ef4444; "
            "font-weight: bold;"
        )

    def handle_worker_finished(self):
        if self._stopping:
            return
        if self.worker is None:
            return
        if self.worker.cleanup_ok:
            self.worker.deleteLater()
            self.worker = None
        else:
            self.stop_video()
        self.cam_status.setText("● CAM-01 · 오류")
        self.cam_status.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #ef4444; "
            "border-radius: 5px; padding: 15px; color: #ef4444; font-size: 22px;"
        )

    def update_metrics(self, data):
        self.metric_current["value"].setText(str(data.get("current_objects", 0)))
        self.metric_total["value"].setText(str(data.get("tracked_total", 0)))

    def add_event(self, event):
        event_type = event.get("type", "unknown")
        person_id = event.get("person_id", "-")
        time_text = event.get("time", datetime.now().strftime("%H:%M:%S"))

        if event_type == "appear":
            self.appear_count += 1
            self.metric_appear["value"].setText(str(self.appear_count))
            desc = f"ID {person_id} 출현"
            color = "#22c55e"
        elif event_type == "disappear":
            self.disappear_count += 1
            self.metric_disappear["value"].setText(str(self.disappear_count))
            desc = f"ID {person_id} 사라짐"
            color = "#f97316"
        elif event_type == "error":
            desc = event.get("message", "오류 발생")
            color = "#ef4444"
        elif event_type == "network_failure":
            desc = event.get("message", "네트워크 장애 감지")
            color = "#facc15"
            self.set_camera_status(
                "● CAM-01 · 네트워크 장애",
                "#facc15",
                "#facc15"
            )
            self.show_network_failure_screen()
        elif event_type == "network_recovered":
            desc = event.get("message", "장애 복구 영상 저장 완료")
            color = "#38bdf8"
            self.set_camera_status(
                "● CAM-01 · LIVE",
                "#22c55e",
                "#22c55e"
            )
        else:
            desc = f"ID {person_id} {event_type}"
            color = "#38bdf8"

        event_box = QFrame()
        event_box.setStyleSheet("background-color: #0f172a; border-radius: 5px;")

        layout = QVBoxLayout(event_box)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        time_label = QLabel(time_text)
        time_label.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: bold;")

        desc_label = QLabel(desc)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        layout.addWidget(time_label)
        layout.addWidget(desc_label)

        self.event_list.insertWidget(0, event_box)
        if self.event_list.count() > 30:
            old_item = self.event_list.takeAt(30)

            if old_item:
                widget = old_item.widget()

                if widget:
                    widget.deleteLater()

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.close_requested.emit()

