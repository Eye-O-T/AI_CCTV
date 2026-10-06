"""인증 창 ↔ 관제 창, 작업 정리 ↔ 서버 종료 순서를 조정한다."""

from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QApplication

from auth.backend_process import BackendProcess
from auth.session_manager import SessionManager
from ui.auth_window import AuthWindow


class AppController(QObject):
    def __init__(self, session=None, backend=None, main_factory=None):
        super().__init__()
        self.session = session or SessionManager(parent=self)
        self.backend = backend or BackendProcess(self)
        self.main_factory = main_factory
        self.auth_window = AuthWindow()
        self.main_window = None
        self.transition = None
        self.return_message = ""
        self.auth_window.submitted.connect(self._submit)
        self.auth_window.retry_requested.connect(self.start)
        self.auth_window.close_requested.connect(self.request_exit)
        self.session.busy_changed.connect(self.auth_window.set_busy)
        self.session.signup_status.connect(self._signup_status)
        self.session.signup_succeeded.connect(self._signup_succeeded)
        self.session.authenticated.connect(self._authenticated)
        self.session.error.connect(self._error)
        self.session.expired.connect(self._expired)
        self.session.signed_out.connect(self._signed_out)
        self.session.notice.connect(self._notice)
        self.backend.ready.connect(self.session.check_signup)
        self.backend.failed.connect(self._backend_failed)
        self.backend.stopped.connect(self._backend_stopped)

    def start(self):
        if self.transition:
            return
        self.auth_window.show()
        self.auth_window.set_mode("checking")
        self.backend.start()

    def _signup_status(self, required):
        if not self.transition:
            self.auth_window.set_mode("signup" if required else "login")

    def _submit(self, mode, username, password, confirmation):
        if mode == "signup":
            self.session.signup(username, password, confirmation)
        else:
            self.session.login(username, password)

    def _signup_succeeded(self):
        self.auth_window.set_mode("login", "관리자 등록이 완료되었습니다. 로그인해 주세요.")

    def _authenticated(self):
        if self.transition:
            self.session.logout()
            return
        try:
            if self.main_factory is None:
                # 로그인 화면은 영상/AI 패키지를 import하지 않고도 사용할 수 있다.
                from ui.gui import CCTVMainWindow

                self.main_factory = CCTVMainWindow
            self.main_window = self.main_factory()
            self.main_window.logout_requested.connect(self.request_logout)
            self.main_window.close_requested.connect(self.request_exit)
            self.main_window.shutdown_complete.connect(self._work_stopped)
            self.main_window.show()
            self.auth_window.hide()
        except Exception:
            self.return_message = "관제 화면을 열 수 없습니다. 영상 처리 의존성을 확인해 주세요."
            self.main_window = None
            self.transition = "logout"
            self.session.logout()

    def _error(self, action, code, message):
        if self.transition:
            return
        if action == "signup-status":
            self.auth_window.set_mode("error", message)
        elif code == "SIGNUP_CLOSED":
            self.auth_window.set_mode("login", message)
        else:
            self.auth_window.message.setText(message)

    def _notice(self, message):
        if self.main_window and not self.transition:
            self.main_window.statusBar().showMessage(message)

    def _expired(self, message):
        self.return_message = message
        self.request_logout()

    def _backend_failed(self, message):
        if self.transition == "exit":
            return
        if self.main_window:
            self.return_message = message
            self.request_logout()
        else:
            self.auth_window.set_mode("error", message)

    def request_logout(self):
        self._begin_transition("logout")

    def request_exit(self):
        if self.transition == "logout":
            self.transition = "exit"
            return
        self._begin_transition("exit")

    def _begin_transition(self, target):
        if self.transition:
            return
        self.transition = target
        if self.main_window:
            self.main_window.begin_shutdown()
        else:
            self.auth_window.set_mode("closing")
            self.session.logout()

    def _work_stopped(self):
        if self.transition:
            self.session.logout()

    def _signed_out(self, warning):
        if self.main_window:
            self.main_window.allow_close = True
            self.main_window.close()
            self.main_window.deleteLater()
            self.main_window = None
        if self.transition == "exit":
            self.backend.stop()
            return
        self.transition = None
        message = "\n".join(value for value in (self.return_message, warning) if value)
        self.return_message = ""
        self.auth_window.set_mode("login" if self.backend.is_ready else "error", message)
        self.auth_window.show()
        self.auth_window.raise_()
        self.auth_window.activateWindow()

    def _backend_stopped(self):
        if self.transition == "exit":
            self.auth_window.allow_close = True
            self.auth_window.close()
            QApplication.instance().quit()
