# gui.py
import os

os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = r"C:\qt_plugins"
os.environ["QT_PLUGIN_PATH"] = r"C:\qt_plugins"

import sys
import cv2
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QDialog,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QImage, QPixmap
from settings_window import SettingsWindow
from video_stream import VideoStream
from person_tracker import PersonTracker
from full_body_checker import FullBodyChecker
from crop_manager import CropManager
from person_state_manager import PersonStateManager
from vlm_worker import VLMWorker
from recording_manager import RecordingManager


class VideoWorker(QThread):
    frame_ready = pyqtSignal(object)
    metrics_ready = pyqtSignal(dict)
    event_ready = pyqtSignal(dict)

    def __init__(self, source=0, use_vlm=False, ai_cctv_path=""):
        super().__init__()
        self.source = source    
        self.running = True
        self.use_vlm = use_vlm

        self.stream = VideoStream(source=self.source)
        self.tracker = PersonTracker(model_path="yolo26s.pt")
        self.full_body_checker = FullBodyChecker()
        self.crop_manager = CropManager()
        self.state_manager = PersonStateManager(disappear_timeout=3.0)
        self.ai_cctv_path = ai_cctv_path
        self.recording_manager = None


        self.vlm_worker = None
        if self.use_vlm:
            self.vlm_worker = VLMWorker(self.state_manager)

    def run(self):
        if not self.stream.open():
            self.event_ready.emit({
                "type": "error",
                "message": "영상 스트림 열기 실패"
            })
            return

        if self.ai_cctv_path:
            fps = self.stream.get_fps()

            self.recording_manager = RecordingManager(
                base_dir=self.ai_cctv_path,
                fps=fps
            )

        if self.use_vlm and self.vlm_worker is not None:
            self.vlm_worker.start()

        while self.running:
            ret, frame = self.stream.read()

            if not ret:
                self.event_ready.emit({
                    "type": "error",
                    "message": "프레임 수신 실패"
                })
                continue
            if self.recording_manager is not None:
                self.recording_manager.write_frame(frame)

            persons = self.tracker.track(frame)

            for person in persons:
                person_id = person["person_id"]
                bbox = person["bbox"]
                conf = person["conf"]

                x1, y1, x2, y2 = map(int, bbox)

                is_full_body = self.full_body_checker.is_full_body_visible(
                    bbox,
                    frame.shape
                )

                self.state_manager.update_person(
                    person_id=person_id,
                    bbox=bbox,
                    is_full_body=is_full_body
                )

                if (
                    self.use_vlm
                    and is_full_body
                    and not self.state_manager.has_crop_saved(person_id)
                ):
                    crop_path = self.crop_manager.save_crop(
                        frame=frame,
                        bbox=bbox,
                        person_id=person_id
                    )

                    if crop_path is not None:
                        self.state_manager.mark_crop_saved(person_id, crop_path)

                        if self.vlm_worker is not None:
                            self.vlm_worker.add_task(person_id, crop_path)

                        self.event_ready.emit({
                            "type": "vlm_queue",
                            "person_id": person_id,
                            "time": datetime.now().strftime("%H:%M:%S")
                        })

                status = self.full_body_checker.get_status_text(
                    bbox,
                    frame.shape
                )

                color = (0, 255, 0) if is_full_body else (0, 0, 255)

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                state = self.state_manager.get_state(person_id)

                vlm_text = ""
                if state is not None and state.get("vlm_done", False):
                    vlm_text = " VLM_DONE"

                label = f"ID:{person_id} {status} {conf:.2f}{vlm_text}"

                cv2.putText(
                    frame,
                    label,
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2
                )

            removed_ids = self.state_manager.remove_disappeared_persons()

            for removed_id in removed_ids:
                self.event_ready.emit({
                    "type": "disappear",
                    "person_id": removed_id,
                    "time": datetime.now().strftime("%H:%M:%S")
                })

            tracked_total = 0
            if hasattr(self.state_manager, "person_states"):
                tracked_total = len(self.state_manager.person_states)

            self.metrics_ready.emit({
                "current_objects": len(persons),
                "tracked_total": tracked_total
            })

            self.frame_ready.emit(frame)

        if self.use_vlm and self.vlm_worker is not None:
            self.vlm_worker.stop()

        if self.recording_manager is not None:
            self.recording_manager.stop_recording()
        self.stream.release()

    def stop(self):
        self.running = False
        self.wait()


class CCTVMainWindow(QMainWindow):
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
        self.use_vlm = True
        self.storage_root_path = ""
        self.ai_cctv_path = ""

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        header_layout = QHBoxLayout()

        title_label = QLabel("Intelligent CCTV Control Center")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold;")

        self.btn_start = QPushButton("START")
        self.btn_start.setStyleSheet(
            "background-color: #166534; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-weight: bold;"
        )
        self.btn_start.clicked.connect(self.start_video)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setStyleSheet(
            "background-color: #7f1d1d; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-weight: bold;"
        )
        self.btn_stop.clicked.connect(self.stop_video)
        self.btn_setting = QPushButton("설정")
        self.btn_setting.setStyleSheet(
            "background-color: #334155; color: white; padding: 8px 20px; "
            "border-radius: 5px; font-weight: bold;"
        )
        self.btn_setting.clicked.connect(self.open_settings)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.btn_start)
        header_layout.addWidget(self.btn_stop)
        header_layout.addWidget(self.btn_setting)

        main_layout.addLayout(header_layout)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(20)
        main_layout.addLayout(body_layout)

        left_panel = QFrame()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")

        left_layout = QVBoxLayout(left_panel)

        cam_label = QLabel("카메라\nRTSP / LAN / USB 입력 상태")
        cam_label.setStyleSheet("color: #94a3b8; font-size: 14px;")
        left_layout.addWidget(cam_label)

        self.cam_status = QLabel("● CAM-01 · 대기 중")
        self.cam_status.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #3b82f6; "
            "border-radius: 5px; padding: 15px; color: #facc15;"
        )
        left_layout.addWidget(self.cam_status)
        left_layout.addStretch()

        body_layout.addWidget(left_panel)

        center_panel = QFrame()
        center_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")
        center_layout = QVBoxLayout(center_panel)

        center_title = QLabel("CAM-01 정문 · 실시간 분석 화면")
        center_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        center_layout.addWidget(center_title)

        self.video_label = QLabel("LIVE VIDEO SURFACE")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #0f172a; border-radius: 5px; "
            "font-size: 24px; color: #334155; font-weight: bold;"
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
        event_label.setStyleSheet("color: #94a3b8; font-size: 14px;")
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
        self.storage_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        right_layout.addWidget(self.storage_label)

        body_layout.addWidget(right_panel)

    def create_metric_box(self, value, label):
        box = QFrame()
        box.setStyleSheet("background-color: #0f172a; border-radius: 5px;")

        layout = QVBoxLayout(box)

        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 28px; font-weight: bold;")

        text_label = QLabel(label)
        text_label.setStyleSheet("color: #94a3b8;")

        layout.addWidget(value_label)
        layout.addWidget(text_label)

        return {
            "box": box,
            "value": value_label,
            "label": text_label
        }

    def start_video(self):
        if self.worker is not None:
            return

        source = self.video_source

        self.worker = VideoWorker(
            source=source,
            use_vlm=self.use_vlm,
            ai_cctv_path=self.ai_cctv_path
        )
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.metrics_ready.connect(self.update_metrics)
        self.worker.event_ready.connect(self.add_event)
        self.worker.start()

        self.cam_status.setText("● CAM-01 · LIVE")
        self.cam_status.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #22c55e; "
            "border-radius: 5px; padding: 15px; color: #22c55e;"
        )

    def stop_video(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

        self.cam_status.setText("● CAM-01 · 중지됨")
        self.cam_status.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #ef4444; "
            "border-radius: 5px; padding: 15px; color: #ef4444;"
        )
    def open_settings(self):
        dialog = SettingsWindow(
            self,
            video_source=self.video_source,
            use_vlm=self.use_vlm,
            storage_root_path=self.storage_root_path,
            ai_cctv_path=self.ai_cctv_path
        )

        if dialog.exec_():
            self.video_source = dialog.selected_source
            self.use_vlm = dialog.use_vlm

            self.storage_root_path = dialog.storage_root_path
            self.ai_cctv_path = dialog.ai_cctv_path

            self.cam_status.setText(
                f"● CAM-01 · 입력 설정 완료: {self.video_source}"
            )

            if self.ai_cctv_path:
                self.storage_label.setText(
                    "저장 경로\n"
                    f"{self.ai_cctv_path}\n\n"
                    "하위 폴더\n"
                    "원본 녹화본\n"
                    "이벤트 CLIP"
                )
            else:
                self.storage_label.setText(
                    "저장 경로\n"
                    "저장 경로가 설정되지 않았습니다.\n\n"
                    "설정 → 저장 설정에서 위치를 선택하세요."
                )

    def update_frame(self, frame):
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
        else:
            desc = f"ID {person_id} {event_type}"
            color = "#38bdf8"

        event_box = QFrame()
        event_box.setStyleSheet("background-color: #0f172a; border-radius: 5px;")

        layout = QVBoxLayout(event_box)

        time_label = QLabel(time_text)
        time_label.setStyleSheet(f"color: {color};")

        desc_label = QLabel(desc)
        desc_label.setStyleSheet("font-size: 15px; font-weight: bold;")

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
        self.stop_video()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CCTVMainWindow()
    window.show()
    sys.exit(app.exec_())