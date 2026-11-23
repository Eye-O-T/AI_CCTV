"""타임라인 이력 전용 비동기 조회. 이벤트 저장 큐와 독립적으로 동작한다."""
from datetime import datetime, timezone
import json
from urllib.parse import urlencode

from PyQt5.QtCore import QObject, QTimer, QUrl
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


KINDS = {'camera_started', 'camera_stopped', 'person_appeared', 'person_disappeared'}
PAGE_SIZE = 50


def event_key(event):
    stamp = datetime.fromisoformat(event['occurred_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('시간대가 필요합니다.')
    return stamp.astimezone(timezone.utc), event['event_id']


def validate_page(payload, cursor):
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise ValueError('이벤트 목록이 필요합니다.')
    items = payload['items']
    if len(items) > PAGE_SIZE:
        raise ValueError('페이지 상한을 초과했습니다.')
    previous = cursor
    for event in items:
        if not isinstance(event, dict) or event.get('event_type') not in KINDS:
            raise ValueError('이벤트 종류를 확인하세요.')
        for field in ('event_id', 'camera_id', 'server_run_id'):
            if type(event.get(field)) is not int or not 0 < event[field] <= 2**63 - 1:
                raise ValueError('이벤트 식별값을 확인하세요.')
        if event['event_type'].startswith('person_'):
            value = event.get('global_person_id')
            if type(value) is not int or not 0 < value <= 2**63 - 1:
                raise ValueError('글로벌 ID를 확인하세요.')
        if not isinstance(event.get('occurred_at'), str):
            raise ValueError('이벤트 시각을 확인하세요.')
        key = event_key(event)
        if previous is not None and key >= previous:
            raise ValueError('페이지 순서 또는 커서를 확인하세요.')
        previous = key
    return items


class EventHistory(QObject):
    def __init__(self, session, panel, parent=None, timeout_ms=10000):
        super().__init__(parent)
        self.session, self.panel = session, panel
        self.network = QNetworkAccessManager(self)
        self.timeout_ms = timeout_ms
        self.reply = None
        self.pending = False
        self.stopped = False
        self.accepting_saved = True
        self.waiting_refresh = False
        self.retried = False
        self.generation = 0
        self.events = {}
        self.cursor = None
        self.has_more = True
        panel.more_requested.connect(self.load_more)
        session.refresh_completed.connect(self._refreshed)
        session.expired.connect(self.close)
        session.signed_out.connect(self.close)

    def load_more(self):
        if (self.stopped or self.pending or not self.has_more or self.session.ending
                or self.session.expiry_reported):
            return
        self.pending = True
        self.retried = False
        self.panel.set_query_state('이벤트를 불러오는 중입니다.', busy=True)
        self._send()

    def _send(self):
        if self.stopped or not self.pending:
            return
        if self.session.ending or self.session.expiry_reported:
            self.stop()
            return
        if not self.session.access_token:
            self._fail('로그인 상태를 확인할 수 없습니다.')
            return
        query = {'limit': PAGE_SIZE}
        if self.cursor is not None:
            query.update(before_occurred_at=self.cursor[0].isoformat(), before_event_id=self.cursor[1])
        request = QNetworkRequest(QUrl(self.session.api.base_url + '/api/v1/events?' + urlencode(query)))
        self.sent_token = self.session.access_token
        request.setRawHeader(b'Authorization', ('Bearer ' + self.sent_token).encode())
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        reply = self.network.get(request)
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
        if generation != self.generation or self.stopped:
            return
        self.reply = None
        if self.session.ending or self.session.expiry_reported:
            self.stop()
            return
        if status == 401:
            if self.retried:
                self._fail('로그인 상태를 확인할 수 없습니다.')
                self.session.expire_access()
            else:
                self.retried = True
                if self.sent_token != self.session.access_token:
                    self._send()
                else:
                    self.waiting_refresh = True
                    if not self.session.busy:
                        self.session.refresh()
            return
        if status != 200 or cancelled:
            self._fail('이벤트를 불러오지 못했습니다. 서버 연결을 확인하고 다시 시도하세요.')
            return
        try:
            items = validate_page(json.loads(raw), self.cursor)
        except (ValueError, TypeError, KeyError, OverflowError):
            self._fail('이벤트 응답을 확인할 수 없습니다. 다시 시도하세요.')
            return
        self.pending = False
        self.events.update((event['event_id'], event) for event in items)
        if items:
            self.cursor = event_key(items[-1])
        self.has_more = len(items) == PAGE_SIZE
        self.panel.set_events(self.events.values())
        self.panel.set_query_state(more=self.has_more)

    def receive_saved(self, event):
        """현재 로그인에서 저장이 확인된 이벤트만 병합한다. 조회 커서는 바꾸지 않는다."""
        if not self.accepting_saved or self.session.ending or self.session.expiry_reported:
            return
        try:
            validate_page({'items': [event]}, None)
        except (ValueError, TypeError, KeyError, OverflowError):
            return
        if event['event_id'] in self.events:
            return
        self.events[event['event_id']] = dict(event)
        self.panel.set_events(self.events.values())

    def close(self, *_):
        self.accepting_saved = False
        self.stop()

    def _fail(self, message):
        self.pending = False
        self.waiting_refresh = False
        self.panel.set_query_state(message, retry=True)

    def _refreshed(self, error):
        if self.stopped or not self.pending or not self.waiting_refresh:
            return
        self.waiting_refresh = False
        if error:
            self._fail('인증 갱신을 확인하지 못했습니다. 다시 시도하세요.')
        else:
            self._send()

    def stop(self, *_):
        # 정상 종료의 저장 큐 마무리 중에는 최종 저장 성공을 계속 받을 수 있다.
        self.stopped = True
        self.generation += 1
        self.pending = self.waiting_refresh = False
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
        self.panel.set_query_state('이벤트 조회가 종료되었습니다.')
