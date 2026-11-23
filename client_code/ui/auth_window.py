"""기존 관제 화면의 남색 테마를 사용하는 초기 설정·로그인 화면."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


class AuthWindow(QWidget):
    submitted = pyqtSignal(str, str, str, str)
    retry_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.mode = "checking"
        self.allow_close = False
        self.setWindowTitle("Intelligent CCTV Control Center · 인증")
        self.resize(640, 680)
        self.setMinimumSize(540, 620)
        self.setStyleSheet("""
            QWidget { background-color: #0f172a; color: #f8fafc; font-family: Arial; }
            QFrame#authCard { background-color: #1e293b; border-radius: 10px; }
            QFrame#authCard QLabel { background: transparent; }
            QLineEdit { background-color: #0f172a; color: #f8fafc; border: 1px solid #334155;
                        border-radius: 6px; padding: 12px; font-size: 16px; }
            QLineEdit:focus { border: 1px solid #38bdf8; }
            QPushButton { background-color: #0e7490; color: white; border: none;
                          border-radius: 6px; padding: 12px; font-size: 18px; font-weight: bold; }
            QPushButton:hover { background-color: #0891b2; }
            QPushButton:disabled { background-color: #334155; color: #94a3b8; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 30, 48, 30)
        outer.setSpacing(20)
        brand = QLabel("Intelligent CCTV\nControl Center")
        brand.setStyleSheet("font-size: 26px; font-weight: bold;")
        brand.setAlignment(Qt.AlignCenter)
        outer.addStretch()
        outer.addWidget(brand)
        card = QFrame()
        card.setObjectName("authCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setStyleSheet("font-size: 14px; color: #94a3b8;")
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        self.username_label, self.username = self._input(layout, "아이디", "4자 이상")
        self.password_label, self.password = self._input(layout, "비밀번호", "4자 이상", True)
        self.confirm_label, self.confirm = self._input(layout, "비밀번호 확인", "비밀번호를 다시 입력하세요", True)
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #facc15; font-size: 14px;")
        layout.addWidget(self.message)
        self.submit_button = QPushButton()
        self.submit_button.clicked.connect(self._submit)
        layout.addWidget(self.submit_button)
        self.retry_button = QPushButton("다시 연결")
        self.retry_button.clicked.connect(self.retry_requested)
        layout.addWidget(self.retry_button)
        outer.addWidget(card)
        outer.addStretch()
        for field in (self.username, self.password, self.confirm):
            field.returnPressed.connect(self._submit)
        self.set_mode("checking")

    def _input(self, layout, text, placeholder, secret=False):
        label = QLabel(text)
        label.setStyleSheet("font-size: 15px; color: #cbd5e1;")
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        if secret:
            field.setEchoMode(QLineEdit.Password)
        layout.addWidget(label)
        layout.addWidget(field)
        return label, field

    def set_mode(self, mode, message=""):
        self.mode = mode
        self.password.clear()
        self.confirm.clear()
        titles = {"checking": "서버 연결 확인", "error": "서버 연결 실패", "signup": "최초 관리자 등록", "login": "관리자 로그인", "closing": "종료 중"}
        descriptions = {"signup": "관리자 계정을 최초 한 번 등록합니다.", "login": "관제를 시작하려면 로그인해 주세요.", "checking": "관리자 등록 상태를 확인하고 있습니다.", "error": "서버 실행 상태를 확인한 뒤 다시 연결해 주세요.", "closing": "진행 중인 요청을 정리하고 있습니다."}
        self.title.setText(titles[mode])
        self.description.setText(descriptions[mode])
        form = mode in ("signup", "login")
        for widget in (self.username_label, self.username, self.password_label, self.password):
            widget.setVisible(form)
        for widget in (self.confirm_label, self.confirm):
            widget.setVisible(mode == "signup")
        self.submit_button.setVisible(form)
        self.submit_button.setText("회원가입" if mode == "signup" else "로그인")
        self.retry_button.setVisible(mode == "error")
        self.message.setText(message)
        self.set_busy(False)
        if form:
            self.username.setFocus()

    def set_busy(self, busy):
        for widget in (self.username, self.password, self.confirm, self.submit_button, self.retry_button):
            widget.setEnabled(not busy)
        if busy:
            self.message.setText("요청을 처리하고 있습니다…")

    def _submit(self):
        if self.mode not in ("signup", "login") or not self.submit_button.isEnabled():
            return
        username, password, confirmation = self.username.text(), self.password.text(), self.confirm.text()
        if len(username) < 4 or len(password) < 4:
            self.message.setText("아이디와 비밀번호를 각각 4자 이상 입력해 주세요.")
            return
        if self.mode == "signup" and password != confirmation:
            self.message.setText("비밀번호와 비밀번호 확인이 일치하지 않습니다.")
            return
        self.submitted.emit(self.mode, username, password, confirmation)
        self.password.clear()
        self.confirm.clear()

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.close_requested.emit()
