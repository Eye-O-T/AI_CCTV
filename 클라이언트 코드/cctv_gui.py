import sys
import cv2
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QFrame, QGridLayout)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QImage, QPixmap

class CCTVMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Intelligent CCTV Control Center")
        self.setGeometry(100, 100, 1600, 900)
        self.setStyleSheet("background-color: #0f172a; color: #f8fafc; font-family: Arial;")

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # 1. Header
        header_layout = QHBoxLayout()
        title_label = QLabel("Intelligent CCTV Control Center")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        
        btn_rec = QPushButton("REC ON")
        btn_rec.setStyleSheet("background-color: #7f1d1d; color: white; padding: 8px 20px; border-radius: 5px; font-weight: bold;")
        btn_settings = QPushButton("설정")
        btn_settings.setStyleSheet("background-color: #334155; color: white; padding: 8px 20px; border-radius: 5px; font-weight: bold;")
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(btn_rec)
        header_layout.addWidget(btn_settings)
        main_layout.addLayout(header_layout)

        # 2. Body Layout
        body_layout = QHBoxLayout()
        body_layout.setSpacing(20)
        main_layout.addLayout(body_layout)

        # --- Left Panel ---
        left_panel = QFrame()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")
        left_layout = QVBoxLayout(left_panel)
        
        cam_label = QLabel("카메라\nRTSP / LAN / USB 입력 상태")
        cam_label.setStyleSheet("color: #94a3b8; font-size: 14px;")
        left_layout.addWidget(cam_label)
        
        cam_btn = QPushButton("● CAM-01 · 정문\n1920x1080 · 24fps · LIVE")
        cam_btn.setStyleSheet("background-color: #0f172a; border: 1px solid #3b82f6; border-radius: 5px; padding: 15px; text-align: left; color: #22c55e;")
        left_layout.addWidget(cam_btn)
        left_layout.addStretch()
        
        body_layout.addWidget(left_panel)

        # --- Center Panel ---
        center_panel = QFrame()
        center_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")
        center_layout = QVBoxLayout(center_panel)
        
        center_title = QLabel("CAM-01 정문 · 실시간 분석 화면")
        center_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        center_layout.addWidget(center_title)

        # Video Surface
        self.video_label = QLabel("LIVE VIDEO SURFACE")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: #0f172a; border-radius: 5px; font-size: 24px; color: #334155; font-weight: bold;")
        self.video_label.setMinimumSize(800, 450)
        center_layout.addWidget(self.video_label, stretch=1)

        # Metrics Surface
        metrics_layout = QHBoxLayout()
        for val, lbl in [("7", "현재 객체"), ("3", "출현"), ("11", "이동"), ("2", "사라짐"), ("0", "None"), ("0", "None")]:
            metric_box = QFrame()
            metric_box.setStyleSheet("background-color: #0f172a; border-radius: 5px;")
            m_layout = QVBoxLayout(metric_box)
            v_label = QLabel(val)
            v_label.setStyleSheet("font-size: 28px; font-weight: bold;")
            l_label = QLabel(lbl)
            l_label.setStyleSheet("color: #94a3b8;")
            m_layout.addWidget(v_label)
            m_layout.addWidget(l_label)
            metrics_layout.addWidget(metric_box)
            
        center_layout.addLayout(metrics_layout)
        body_layout.addWidget(center_panel, stretch=1)

        # --- Right Panel ---
        right_panel = QFrame()
        right_panel.setFixedWidth(350)
        right_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")
        right_layout = QVBoxLayout(right_panel)
        
        event_label = QLabel("이벤트 타임라인\n출현 · 이동 · 사라짐 중심")
        event_label.setStyleSheet("color: #94a3b8; font-size: 14px;")
        right_layout.addWidget(event_label)
        
        event_box = QFrame()
        event_box.setStyleSheet("background-color: #0f172a; border-radius: 5px;")
        e_layout = QVBoxLayout(event_box)
        e_time = QLabel("14:22:18")
        e_time.setStyleSheet("color: #22c55e;")
        e_desc = QLabel("ID 014 - Person")
        e_desc.setStyleSheet("font-size: 16px; font-weight: bold;")
        e_layout.addWidget(e_time)
        e_layout.addWidget(e_desc)
        right_layout.addWidget(event_box)
        
        right_layout.addStretch()
        
        storage_label = QLabel("저장 경로\nc://tmp1/tmp2/tmp3/cctv\n\n저장 영상 총 용량 13GB/52GB")
        storage_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        right_layout.addWidget(storage_label)

        body_layout.addWidget(right_panel)

        # OpenCV Video Capture Setup
        self.cap = cv2.VideoCapture(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(30) # 30ms (~33 fps)

    def update_frame(self):
        ret, frame = self.cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = frame.shape
            bytes_per_line = ch * w
            
            qt_img = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qt_img)
            
            # Scale pixmap to fit the label while maintaining aspect ratio
            scaled_pixmap = pixmap.scaled(self.video_label.width(), self.video_label.height(), Qt.KeepAspectRatio)
            self.video_label.setPixmap(scaled_pixmap)

    def closeEvent(self, event):
        if self.cap.isOpened():
            self.cap.release()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = CCTVMainWindow()
    window.show()
    sys.exit(app.exec_())