from PyQt5.QtWidgets import (
    QDialog,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QStackedWidget,
    QRadioButton,
    QLineEdit,
    QButtonGroup,
)
from PyQt5.QtCore import Qt


class SettingsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("설정")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setFixedSize(1100, 650)
        self.setStyleSheet(
            "background-color: #0f172a; color: #f8fafc; font-family: Arial;"
        )

        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        menu_panel = QFrame()
        menu_panel.setFixedWidth(200)
        menu_panel.setStyleSheet("background-color: #1e293b; border-radius: 10px;")

        menu_layout = QVBoxLayout(menu_panel)
        menu_layout.setSpacing(10)

        title = QLabel("설정")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        menu_layout.addWidget(title)

        self.btn_input = self.create_menu_button("입력 설정")
        self.btn_storage = self.create_menu_button("저장 설정")
        self.btn_ai = self.create_menu_button("AI 분석 설정")
        self.btn_event = self.create_menu_button("이벤트 설정")
        self.btn_system = self.create_menu_button("시스템 설정")

        menu_layout.addWidget(self.btn_input)
        menu_layout.addWidget(self.btn_storage)
        menu_layout.addWidget(self.btn_ai)
        menu_layout.addWidget(self.btn_event)
        menu_layout.addWidget(self.btn_system)
        menu_layout.addStretch()

        self.pages = QStackedWidget()
        self.pages.setStyleSheet("background-color: #1e293b; border-radius: 10px;")

        self.pages.addWidget(self.create_input_page())
        self.pages.addWidget(self.create_empty_page("저장 설정", "저장 위치 / 원본 영상 저장 시간 설정 영역"))
        self.pages.addWidget(self.create_empty_page("AI 분석 설정", "YOLO / VLM 가동 여부 설정 영역"))
        self.pages.addWidget(self.create_empty_page("이벤트 설정", "출현 조건 / 사라짐 조건 / 쿨타임 설정 영역"))
        self.pages.addWidget(self.create_empty_page("시스템 설정", "로그 / 자동 실행 / 초기화 설정 영역"))

        self.btn_input.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.btn_storage.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        self.btn_ai.clicked.connect(lambda: self.pages.setCurrentIndex(2))
        self.btn_event.clicked.connect(lambda: self.pages.setCurrentIndex(3))
        self.btn_system.clicked.connect(lambda: self.pages.setCurrentIndex(4))

        main_layout.addWidget(menu_panel)
        main_layout.addWidget(self.pages, stretch=1)

    def create_menu_button(self, text):
        button = QPushButton(text)
        button.setFixedHeight(45)
        button.setStyleSheet(
            "background-color: #0f172a; color: #f8fafc; "
            "border-radius: 6px; font-size: 15px; text-align: left; padding-left: 15px;"
        )
        return button

    def create_input_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(18)

        title = QLabel("입력 설정")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("카메라 입력 방식을 선택하세요.")
        desc.setStyleSheet("font-size: 15px; color: #94a3b8;")
        layout.addWidget(desc)

        input_box = QFrame()
        input_box.setStyleSheet("background-color: #0f172a; border-radius: 8px;")
        input_layout = QVBoxLayout(input_box)
        input_layout.setContentsMargins(20, 20, 20, 20)
        input_layout.setSpacing(15)

        mode_label = QLabel("카메라 입력 방식")
        mode_label.setStyleSheet("font-size: 17px; font-weight: bold;")
        input_layout.addWidget(mode_label)

        self.radio_webcam = QRadioButton("웹캠 사용")
        self.radio_rtsp = QRadioButton("RTSP 사용")

        radio_style = """
            QRadioButton {
                font-size: 15px;
                color: #f8fafc;
                spacing: 8px;
            }
        """
        self.radio_webcam.setStyleSheet(radio_style)
        self.radio_rtsp.setStyleSheet(radio_style)

        self.input_mode_group = QButtonGroup(self)
        self.input_mode_group.addButton(self.radio_webcam)
        self.input_mode_group.addButton(self.radio_rtsp)

        self.radio_webcam.setChecked(True)

        input_layout.addWidget(self.radio_webcam)

        webcam_info = QLabel("웹캠 선택 시 OpenCV 기본 카메라 번호 0번을 사용합니다.")
        webcam_info.setStyleSheet("font-size: 14px; color: #94a3b8; padding-left: 24px;")
        input_layout.addWidget(webcam_info)

        input_layout.addWidget(self.radio_rtsp)

        rtsp_label = QLabel("RTSP 주소")
        rtsp_label.setStyleSheet("font-size: 15px; font-weight: bold; padding-left: 24px;")
        input_layout.addWidget(rtsp_label)

        self.rtsp_input = QLineEdit()
        self.rtsp_input.setMinimumHeight(40)
        self.rtsp_input.setPlaceholderText("예: rtsp://192.168.10.2:8554/stream")
        self.rtsp_input.setEnabled(False)
        self.rtsp_input.setStyleSheet(
            "background-color: #1e293b; color: #f8fafc; "
            "border: 1px solid #334155; border-radius: 6px; "
            "padding: 10px; font-size: 14px;"
        )
        input_layout.addWidget(self.rtsp_input)

        self.radio_webcam.toggled.connect(self.update_input_mode)
        self.radio_rtsp.toggled.connect(self.update_input_mode)

        layout.addWidget(input_box)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_save = QPushButton("저장")
        self.btn_save.setStyleSheet(
            "background-color: #2563eb; color: white; padding: 10px 24px; "
            "border-radius: 6px; font-weight: bold;"
        )
        self.btn_save.clicked.connect(self.save_input_settings)

        button_layout.addWidget(self.btn_save)
        layout.addLayout(button_layout)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet("font-size: 14px; color: #22c55e;")
        layout.addWidget(self.result_label)

        layout.addStretch()

        return page

    def update_input_mode(self):
        if self.radio_rtsp.isChecked():
            self.rtsp_input.setEnabled(True)
        else:
            self.rtsp_input.setEnabled(False)

    def save_input_settings(self):
        if self.radio_webcam.isChecked():
            source = 0
            self.result_label.setText("입력 설정 저장: 웹캠 사용, source = 0")
        else:
            rtsp_url = self.rtsp_input.text().strip()

            if not rtsp_url:
                self.result_label.setStyleSheet("font-size: 14px; color: #ef4444;")
                self.result_label.setText("RTSP 주소를 입력하세요.")
                return

            source = rtsp_url
            self.result_label.setStyleSheet("font-size: 14px; color: #22c55e;")
            self.result_label.setText(f"입력 설정 저장: RTSP 사용, source = {source}")

        self.selected_source = source
        self.accept()

    def create_empty_page(self, title_text, desc_text):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)

        title = QLabel(title_text)
        title.setStyleSheet("font-size: 24px; font-weight: bold;")

        desc = QLabel(desc_text)
        desc.setStyleSheet("font-size: 16px; color: #94a3b8;")
        desc.setAlignment(Qt.AlignTop)

        layout.addWidget(title)
        layout.addSpacing(20)
        layout.addWidget(desc)
        layout.addStretch()

        return page