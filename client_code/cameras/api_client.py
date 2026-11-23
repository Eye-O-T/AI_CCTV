"""카메라 관리용 비동기 HTTP. 토큰 소유·갱신은 기존 SessionManager에 위임한다."""
import json

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


def valid_camera(value, detail=False):
    if not isinstance(value, dict):
        return False
    if (type(value.get('camera_id')) is not int or value['camera_id'] < 1
            or not isinstance(value.get('name'), str)
            or value.get('camera_type') not in ('webcam', 'edge_node')
            or type(value.get('is_enabled')) is not bool):
        return False
    if detail:
        if value['camera_type'] == 'webcam':
            return type(value.get('webcam_index')) is int and value['webcam_index'] >= 0
        return isinstance(value.get('rtsp_url'), str) and bool(value['rtsp_url']) and (
            value.get('edge_api_base_url') is None or isinstance(value['edge_api_base_url'], str))
    return True


class CameraApiClient(QObject):
    completed = pyqtSignal(object, str, str)
    busy_changed = pyqtSignal(bool)

    def __init__(self, session, parent=None, timeout_ms=10000):
        super().__init__(parent)
        self.session = session
        self.base_url = session.api.base_url
        self.network = QNetworkAccessManager(self)
        self.timeout_ms = timeout_ms
        self.reply = None
        self.pending = None
        self.waiting_refresh = False
        self.retried = False
        self.generation = 0
        session.refresh_completed.connect(self._refreshed)
        session.expired.connect(self.cancel)
        session.signed_out.connect(self.cancel)

    def request(self, method, path='', body=None):
        if self.pending is not None or self.session.ending or self.session.expiry_reported:
            return False
        self.pending = (method, path, body)
        self.retried = False
        self.busy_changed.emit(True)
        self._send()
        return True

    def _send(self):
        if not self.pending:
            return
        if self.session.ending or self.session.expiry_reported or not self.session.access_token:
            self._finish(None, 'INVALID_ACCESS_TOKEN', '')
            return
        method, path, body = self.pending
        self.sent_token = self.session.access_token
        request = QNetworkRequest(QUrl(self.base_url + '/api/v1/cameras' + path))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.sent_token).encode())
        request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        data = json.dumps(body, ensure_ascii=True).encode() if body is not None else b''
        reply = self.network.sendCustomRequest(request, method.encode(), data)
        self.reply = reply
        generation = self.generation
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        timer.start(self.timeout_ms)
        reply.finished.connect(lambda: self._received(reply, timer, generation))

    def _received(self, reply, timer, generation):
        timer.stop()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll())
        cancelled = reply.error() == QNetworkReply.OperationCanceledError
        reply.deleteLater()
        if generation != self.generation:
            return
        self.reply = None
        if self.session.ending or self.session.expiry_reported:
            self.cancel()
            return
        if status is None or cancelled:
            self._finish(None, 'CONNECTION_ERROR', '')
            return
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            self._finish(None, 'INVALID_RESPONSE', '')
            return
        if status == 401:
            if self.retried:
                self._finish(None, 'INVALID_ACCESS_TOKEN', '')
                self.session.expire_access()
                return
            self.retried = True
            if self.sent_token != self.session.access_token:
                self._send()
            else:
                self.waiting_refresh = True
                if not self.session.busy:
                    self.session.refresh()
            return
        if not 200 <= status < 300:
            detail = payload.get('error', {})
            code = detail.get('code') if isinstance(detail, dict) else None
            field = detail.get('field') if isinstance(detail, dict) else None
            allowed = {'name', 'camera_type', 'webcam_index', 'rtsp_url', 'edge_api_base_url', 'is_enabled', 'camera_id'}
            self._finish(None, code if isinstance(code, str) else 'SERVER_ERROR',
                         field if isinstance(field, str) and field in allowed else '')
            return
        self._finish(payload, '', '')

    def _refreshed(self, error):
        if not self.waiting_refresh or not self.pending:
            return
        self.waiting_refresh = False
        if error:
            self._finish(None, error, '')
        else:
            self._send()

    def _finish(self, payload, error, field):
        self.pending = None
        self.waiting_refresh = False
        self.busy_changed.emit(False)
        self.completed.emit(payload, error, field)

    def cancel(self, *_):
        self.generation += 1
        self.pending = None
        self.waiting_refresh = False
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
        self.busy_changed.emit(False)
