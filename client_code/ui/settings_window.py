import os

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
    QCheckBox,
    QFileDialog,
)
from PyQt5.QtCore import Qt


class SettingsWindow(QDialog):
    def __init__(
        self,
        parent=None,
        video_source=0,
        use_yolo=True,
        use_vlm=False,
        storage_root_path="",
        ai_cctv_path="",
        original_segment_seconds=10,
        clip_max_seconds=10,
        session=None
    ):
        super().__init__(parent)

        self.session = session
        self.camera_page = None
        self.selected_source = video_source
        self.use_yolo = use_yolo
        self.use_vlm = False
        self.storage_root_path = storage_root_path
        self.ai_cctv_path = ai_cctv_path
        self.original_segment_seconds = original_segment_seconds
        self.clip_max_seconds = clip_max_seconds

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

        self.btn_basic = self.create_menu_button("분석 설정")
        self.btn_storage = self.create_menu_button("저장 설정")

        if self.session is not None:
            from ui.camera_management import CameraManagementPage
            self.camera_page = CameraManagementPage(self.session, self)
            self.btn_cameras = self.create_menu_button("카메라 관리")
            menu_layout.addWidget(self.btn_cameras)
        menu_layout.addWidget(self.btn_basic)
        menu_layout.addWidget(self.btn_storage)
        menu_layout.addStretch()

        self.pages = QStackedWidget()
        self.pages.setStyleSheet("background-color: #1e293b; border-radius: 10px;")

        self.pages.addWidget(self.create_basic_page())
        self.pages.addWidget(self.create_storage_page())

        self.btn_basic.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.btn_storage.clicked.connect(lambda: self.pages.setCurrentIndex(1))

        if self.camera_page is not None:
            self.pages.addWidget(self.camera_page)
            self.btn_cameras.clicked.connect(lambda: self.pages.setCurrentWidget(self.camera_page))
            self.pages.setCurrentWidget(self.camera_page)
            self.finished.connect(self.camera_page.close_requests)
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

    def create_basic_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addWidget(QLabel("분석 설정"))
        layout.addWidget(QLabel("분석 옵션은 이 프로그램의 설정입니다. 카메라 등록 정보와 별도로 적용합니다."))
        self.yolo_checkbox = QCheckBox("YOLO 사람 탐지/추적 사용")
        self.yolo_checkbox.setChecked(self.use_yolo)
        self.vlm_checkbox = QCheckBox("VLM 의상 분석 (이번 개발 단계에서는 사용하지 않음)")
        self.vlm_checkbox.setChecked(self.use_yolo and self.use_vlm)
        self.yolo_checkbox.toggled.connect(self.update_ai_mode)
        self.update_ai_mode()
        layout.addWidget(self.yolo_checkbox)
        layout.addWidget(self.vlm_checkbox)
        self.btn_save = QPushButton("분석 설정 적용")
        self.btn_save.clicked.connect(self.save_basic_settings)
        layout.addWidget(self.btn_save)
        layout.addStretch()
        return page

    def update_ai_mode(self):
        self.vlm_checkbox.setEnabled(False)
        self.vlm_checkbox.setChecked(False)

    def save_basic_settings(self):
        self.use_yolo = self.yolo_checkbox.isChecked()
        self.use_vlm = False
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

    def create_storage_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(18)

        title = QLabel("저장 설정")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("원본 영상과 이벤트 클립 저장 방식을 설정합니다.")
        desc.setStyleSheet("font-size: 15px; color: #94a3b8;")
        layout.addWidget(desc)

        storage_box = QFrame()
        storage_box.setStyleSheet("background-color: #0f172a; border-radius: 8px;")

        storage_layout = QVBoxLayout(storage_box)
        storage_layout.setContentsMargins(20, 20, 20, 20)
        storage_layout.setSpacing(18)

        path_label = QLabel("영상 저장 경로 설정")
        path_label.setStyleSheet("font-size: 17px; font-weight: bold;")
        storage_layout.addWidget(path_label)

        path_desc = QLabel(
            "위치를 선택하면 해당 위치에 AI_CCTV 폴더가 생성되고,\n"
            "하위 폴더로 원본 녹화본 / 이벤트 폴더가 생성됩니다."
        )
        path_desc.setStyleSheet("font-size: 14px; color: #94a3b8;")
        storage_layout.addWidget(path_desc)

        path_row = QHBoxLayout()

        self.storage_path_input = QLineEdit()
        if self.ai_cctv_path:
            self.storage_path_input.setText(self.ai_cctv_path)
        self.storage_path_input.setReadOnly(True)
        self.storage_path_input.setMinimumHeight(40)
        self.storage_path_input.setPlaceholderText("저장 위치를 선택하세요.")
        self.storage_path_input.setStyleSheet(
            "background-color: #1e293b; color: #f8fafc; "
            "border: 1px solid #334155; border-radius: 6px; "
            "padding: 10px; font-size: 14px;"
        )

        self.btn_select_storage_path = QPushButton("위치 선택")
        self.btn_select_storage_path.setMinimumHeight(40)
        self.btn_select_storage_path.setStyleSheet(
            "background-color: #2563eb; color: white; padding: 8px 18px; "
            "border-radius: 6px; font-weight: bold;"
        )
        self.btn_select_storage_path.clicked.connect(self.select_storage_path)

        path_row.addWidget(self.storage_path_input, stretch=1)
        path_row.addWidget(self.btn_select_storage_path)
        storage_layout.addLayout(path_row)

        original_unit_label = QLabel("원본 영상 저장 단위")
        original_unit_label.setStyleSheet(
            "font-size: 17px; font-weight: bold; margin-top: 10px;"
        )
        storage_layout.addWidget(original_unit_label)

        self.original_10s_radio = QRadioButton("10초")
        self.original_30s_radio = QRadioButton("30초")
        self.original_1m_radio = QRadioButton("1분")

        self.original_unit_group = QButtonGroup(self)
        self.original_unit_group.addButton(self.original_10s_radio)
        self.original_unit_group.addButton(self.original_30s_radio)
        self.original_unit_group.addButton(self.original_1m_radio)

        if self.original_segment_seconds == 10:
            self.original_10s_radio.setChecked(True)
        elif self.original_segment_seconds == 30:
            self.original_30s_radio.setChecked(True)
        else:
            self.original_1m_radio.setChecked(True)

        original_row = QHBoxLayout()
        for radio in [
            self.original_10s_radio,
            self.original_30s_radio,
            self.original_1m_radio,
        ]:
            radio.setStyleSheet(
                "QRadioButton { font-size: 15px; color: #f8fafc; spacing: 8px; }"
            )
            original_row.addWidget(radio)

        original_row.addStretch()
        storage_layout.addLayout(original_row)

        clip_length_label = QLabel("클립당 최대 클립 길이")
        clip_length_label.setStyleSheet(
            "font-size: 17px; font-weight: bold; margin-top: 10px;"
        )
        storage_layout.addWidget(clip_length_label)

        self.clip_10s_radio = QRadioButton("10초")
        self.clip_30s_radio = QRadioButton("30초")
        self.clip_full_radio = QRadioButton("전체(이벤트 전체)")

        self.clip_length_group = QButtonGroup(self)
        self.clip_length_group.addButton(self.clip_10s_radio)
        self.clip_length_group.addButton(self.clip_30s_radio)
        self.clip_length_group.addButton(self.clip_full_radio)

        if self.clip_max_seconds == 10:
            self.clip_10s_radio.setChecked(True)
        elif self.clip_max_seconds == 30:
            self.clip_30s_radio.setChecked(True)
        else:
            self.clip_full_radio.setChecked(True)

        clip_row = QHBoxLayout()
        for radio in [
            self.clip_10s_radio,
            self.clip_30s_radio,
            self.clip_full_radio,
        ]:
            radio.setStyleSheet(
                "QRadioButton { font-size: 15px; color: #f8fafc; spacing: 8px; }"
            )
            clip_row.addWidget(radio)

        clip_row.addStretch()
        storage_layout.addLayout(clip_row)

        self.storage_result_label = QLabel("")
        self.storage_result_label.setStyleSheet("font-size: 14px; color: #22c55e;")
        storage_layout.addWidget(self.storage_result_label)

        layout.addWidget(storage_box)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_storage_save = QPushButton("저장")
        self.btn_storage_save.setStyleSheet(
            "background-color: #2563eb; color: white; padding: 10px 24px; "
            "border-radius: 6px; font-weight: bold;"
        )
        self.btn_storage_save.clicked.connect(self.save_storage_settings)

        button_layout.addWidget(self.btn_storage_save)
        layout.addLayout(button_layout)

        self.storage_save_result = QLabel("")
        self.storage_save_result.setStyleSheet("font-size: 14px; color: #22c55e;")
        layout.addWidget(self.storage_save_result)

        layout.addStretch()

        return page

    def select_storage_path(self):
        selected_path = QFileDialog.getExistingDirectory(
            self,
            "영상 저장 위치 선택"
        )

        if not selected_path:
            return

        ai_cctv_path = os.path.join(selected_path, "AI_CCTV")
        original_path = os.path.join(ai_cctv_path, "원본 녹화본")
        event_clip_path = os.path.join(ai_cctv_path, "이벤트")

        os.makedirs(original_path, exist_ok=True)
        os.makedirs(event_clip_path, exist_ok=True)

        self.storage_root_path = selected_path
        self.ai_cctv_path = ai_cctv_path

        self.storage_path_input.setText(ai_cctv_path)
        self.storage_result_label.setText("저장 폴더가 생성되었습니다.")

    def save_storage_settings(self):
        if not self.ai_cctv_path:
            self.storage_save_result.setStyleSheet(
                "font-size: 14px; color: #ef4444;"
            )
            self.storage_save_result.setText("저장 경로를 먼저 선택하세요.")
            return

        if self.original_10s_radio.isChecked():
            self.original_segment_seconds = 10
        elif self.original_30s_radio.isChecked():
            self.original_segment_seconds = 30
        else:
            self.original_segment_seconds = 60

        if self.clip_10s_radio.isChecked():
            self.clip_max_seconds = 10
        elif self.clip_30s_radio.isChecked():
            self.clip_max_seconds = 30
        else:
            self.clip_max_seconds = None

        self.storage_save_result.setStyleSheet(
            "font-size: 14px; color: #22c55e;"
        )
        self.storage_save_result.setText("저장 설정이 적용되었습니다.")

        self.accept()
