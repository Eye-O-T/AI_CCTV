"""토큰을 메모리에만 보관하고 갱신·로그아웃을 직렬화한다."""

import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from auth.api_client import AuthApiClient


MESSAGES = {
    "CONNECTION_ERROR": "서버에 연결할 수 없거나 응답 시간이 초과되었습니다. 서버 실행 상태를 확인해 주세요.",
    "INVALID_RESPONSE": "서버 응답을 확인할 수 없습니다. 서버 주소와 실행 상태를 확인해 주세요.",
    "INVALID_CREDENTIALS": "아이디 또는 비밀번호가 올바르지 않습니다.",
    "SIGNUP_CLOSED": "관리자 계정이 이미 있습니다. 로그인해 주세요.",
    "PASSWORD_CONFIRM_MISMATCH": "비밀번호와 비밀번호 확인이 일치하지 않습니다.",
    "VALIDATION_ERROR": "입력한 정보를 확인해 주세요.",
}


class SessionManager(QObject):
    signup_status = pyqtSignal(bool)
    signup_succeeded = pyqtSignal()
    authenticated = pyqtSignal()
    error = pyqtSignal(str, str, str)
    notice = pyqtSignal(str)
    expired = pyqtSignal(str)
    signed_out = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    refresh_completed = pyqtSignal(str)

    def __init__(self, api=None, parent=None):
        super().__init__(parent)
        self.api = api or AuthApiClient(parent=self)
        self.api.completed.connect(self._completed)
        self.access_token = None
        self.refresh_token = None
        self.user = None
        self.busy = False
        self.ending = False
        self.deadline = 0
        self.request_started = 0
        self.expiry_reported = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh)
        self.deadline_timer = QTimer(self)
        self.deadline_timer.setInterval(1000)
        self.deadline_timer.timeout.connect(self._check_deadline)

    def _request(self, action, body=None):
        if self.busy:
            return False
        self.busy = True
        self.request_started = time.monotonic()
        self.busy_changed.emit(True)
        self.api.request(action, body)
        return True

    def check_signup(self):
        if not self.ending:
            self._request("signup-status")

    def signup(self, username, password, confirmation):
        if not self.ending:
            self._request("signup", {"username": username, "password": password, "password_confirm": confirmation})

    def login(self, username, password):
        if not self.ending:
            self._request("login", {"username": username, "password": password})

    def refresh(self):
        if self.ending or self.expiry_reported or not self.refresh_token:
            return
        if self._check_deadline():
            return
        if not self._request("refresh", {"refresh_token": self.refresh_token}):
            self.refresh_timer.start(1000)

    def expire_access(self):
        if not self.ending and not self.expiry_reported:
            self._expire("로그인 상태를 확인할 수 없습니다. 다시 로그인해 주세요.")

    def logout(self):
        self.ending = True
        self.refresh_timer.stop()
        self.deadline_timer.stop()
        # 진행 중 로그인/갱신 응답의 최신 Refresh를 먼저 받은 뒤 폐기한다.
        if not self.busy:
            self._logout_latest()

    def _logout_latest(self):
        if self.refresh_token:
            self._request("logout", {"refresh_token": self.refresh_token})
        else:
            self._finish_logout("")

    def _finish_logout(self, warning):
        self.refresh_timer.stop()
        self.deadline_timer.stop()
        self.access_token = self.refresh_token = self.user = None
        self.deadline = 0
        self.ending = False
        self.expiry_reported = False
        self.signed_out.emit(warning)

    def _check_deadline(self):
        if self.refresh_token and time.monotonic() >= self.deadline and not self.expiry_reported:
            self._expire("로그인 유지 시간이 끝났습니다. 작업 정리 후 다시 로그인해 주세요.")
            return True
        return self.expiry_reported

    def _expire(self, message):
        self.expiry_reported = True
        self.refresh_timer.stop()
        self.deadline_timer.stop()
        self.expired.emit(message)

    def _accept_tokens(self, payload, login):
        if not isinstance(payload, dict):
            return False
        if not all(isinstance(payload.get(key), str) and payload[key] for key in ("access_token", "refresh_token")):
            return False
        if payload.get("token_type") != "bearer":
            return False
        if not all(type(payload.get(key)) is int and payload[key] > 0 for key in ("access_expires_in", "refresh_expires_in")):
            return False
        if login and (not isinstance(payload.get("user"), dict) or not isinstance(payload["user"].get("username"), str)):
            return False
        self.access_token = payload["access_token"]
        self.refresh_token = payload["refresh_token"]
        candidate = self.request_started + payload["refresh_expires_in"]
        self.deadline = candidate if login else min(self.deadline, candidate)
        if login:
            self.user = payload["user"]
            self.expiry_reported = False
        if not self.ending and not self.expiry_reported:
            elapsed = time.monotonic() - self.request_started
            delay = max(1, payload["access_expires_in"] - elapsed - 30)
            self.refresh_timer.start(int(delay * 1000))
            self.deadline_timer.start()
        return True

    def _completed(self, action, payload, error):
        # 처리 중에는 busy를 유지하여 화면 전환 콜백의 재진입을 막는다.
        if not error and action in ("login", "refresh"):
            if not self._accept_tokens(payload, action == "login"):
                error = "INVALID_RESPONSE"
        self.busy = False
        self.busy_changed.emit(False)
        if action == "logout":
            warning = "서버 로그아웃을 확인하지 못했습니다. 이 PC의 토큰은 삭제했습니다." if error else ""
            self._finish_logout(warning)
            return
        if self.ending:
            self._logout_latest()
            return
        if action == "refresh":
            self.refresh_completed.emit(error)
        if error:
            if action == "refresh":
                if error in ("CONNECTION_ERROR", "SERVER_ERROR", "INTERNAL_SERVER_ERROR") and not self._check_deadline():
                    self.notice.emit("인증 서버 연결을 재시도하고 있습니다.")
                    self.refresh_timer.start(10000)
                else:
                    self._expire("로그인 상태가 만료되었거나 확인되지 않습니다. 작업 정리 후 다시 로그인해 주세요.")
            else:
                self.error.emit(action, error, MESSAGES.get(error, "서버 요청 처리에 실패했습니다. 다시 시도해 주세요."))
            return
        if action == "signup-status":
            if type(payload.get("signup_required")) is bool:
                self.signup_status.emit(payload["signup_required"])
            else:
                self.error.emit(action, "INVALID_RESPONSE", MESSAGES["INVALID_RESPONSE"])
        elif action == "signup":
            self.signup_succeeded.emit()
        elif action == "login":
            self.authenticated.emit()
        elif action == "refresh":
            self.notice.emit("")
