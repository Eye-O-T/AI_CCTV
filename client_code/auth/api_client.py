"""Qt 이벤트 루프 기반 HTTP. GUI 스레드를 막지 않고 제한 시간 내 완료한다."""

import json

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class AuthApiClient(QObject):
    completed = pyqtSignal(str, object, str)

    def __init__(self, base_url=None, parent=None, timeout_ms=10000):
        super().__init__(parent)
        self.base_url = (base_url or "http://127.0.0.1:9000").rstrip("/")
        self.network = QNetworkAccessManager(self)
        self.timeout_ms = timeout_ms
        self.reply = None

    def request(self, action, body=None):
        if self.reply is not None:
            return False
        request = QNetworkRequest(QUrl(f"{self.base_url}/api/v1/auth/{action}"))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        if action == "signup-status":
            reply = self.network.get(request)
        else:
            reply = self.network.post(request, json.dumps(body, ensure_ascii=True).encode("utf-8"))
        self.reply = reply
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        timer.start(self.timeout_ms)
        reply.finished.connect(lambda: self._finished(action, reply, timer))
        return True

    def _finished(self, action, reply, timer):
        timer.stop()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll())
        error = ""
        payload = None
        if status is None or reply.error() == QNetworkReply.OperationCanceledError:
            error = "CONNECTION_ERROR"
        elif status == 204 and action == "logout":
            payload = {}
        else:
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                error = "INVALID_RESPONSE"
            if not error and not isinstance(payload, dict):
                error = "INVALID_RESPONSE"
            if not error and not 200 <= status < 300:
                detail = payload.get("error", {})
                error = detail.get("code", "SERVER_ERROR") if isinstance(detail, dict) else "SERVER_ERROR"
                if not isinstance(error, str):
                    error = "SERVER_ERROR"
        self.reply = None
        reply.deleteLater()
        # 서버가 보낸 오류 문구/원본 입력은 UI나 로그에 그대로 내보내지 않는다.
        self.completed.emit(action, payload if not error else None, error)
