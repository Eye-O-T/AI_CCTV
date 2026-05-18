from PyQt5.QtWidgets import (
    QDialog,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QStackedWidget,
)
from PyQt5.QtCore import Qt


class SettingsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("설정")
        self.setFixedSize(800, 500)
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

        self.pages.addWidget(self.create_page("입력 설정", "USB / RTSP / 카메라 번호 설정 영역"))
        self.pages.addWidget(self.create_page("저장 설정", "저장 위치 / 원본 영상 저장 시간 설정 영역"))
        self.pages.addWidget(self.create_page("AI 분석 설정", "YOLO / VLM 가동 여부 설정 영역"))
        self.pages.addWidget(self.create_page("이벤트 설정", "출현 조건 / 사라짐 조건 / 쿨타임 설정 영역"))
        self.pages.addWidget(self.create_page("시스템 설정", "로그 / 자동 실행 / 초기화 설정 영역"))

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

    def create_page(self, title_text, desc_text):
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