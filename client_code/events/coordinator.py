"""GUI의 저장 설정·결과 수신·제한 시간 종료를 조정한다."""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from events.store import EventStore
from workers.cnn_worker import CNNWorker


MEDIA = ('crop_path', 'clip_path', 'trajectory_path')


@dataclass
class PersonEvent:
    identity: dict
    task_id: object
    first_seen: datetime
    last_seen: datetime
    revision: int
    media: dict
    finished: bool = False
    natural: bool = False
    event_id: int | None = None
    saved_media: dict = field(default_factory=lambda: dict.fromkeys(MEDIA))
    inflight: str | None = None
    failed: bool = False
    create_without_media: bool = False
    failed_media_revision: int = -1
    disappeared: bool = False
    cnn_source_path: Path | None = None
    cnn_attempted: bool = False
    server_run_id: int | None = None


class EventCoordinator(QObject):
    storage_changed = pyqtSignal(str, bool)
    changed = pyqtSignal()
    notice = pyqtSignal(str)
    settings_finished = pyqtSignal(bool, str)
    drained = pyqtSignal(str)
    event_saved = pyqtSignal(object)

    def __init__(self, session, parent=None, store_factory=EventStore, cnn_factory=CNNWorker):
        super().__init__(parent)
        self.session = session
        self.root = ''
        self.locked = False
        self.loaded = False
        self.settings_pending = False
        self.closing = False
        self.finished = False
        self.failures = 0
        self.can_change = lambda: True
        self._pending = {}
        self._persons = {}
        self._person_requests = {}
        self._cnn_factory = cnn_factory
        self.cnn_worker = None
        self._cnn_jobs = {}
        self._cnn_requests = {}
        self._shutdown_expired = False
        self._active_executions = set()
        self._closed_persons = OrderedDict()
        self._deadline = float('inf')
        self.store = store_factory(session.api.base_url, session.access_token)
        self.store.start()
        self.destroyed.connect(self.store.abort)
        session.refresh_completed.connect(self._refreshed)
        session.signed_out.connect(self._signed_out)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.refresh_storage()

    @property
    def ready(self):
        return self.loaded and bool(self.root) and not self.settings_pending and not self.closing

    def _refreshed(self, error):
        if not error and not self.finished:
            self.store.set_access_token(self.session.access_token)

    def _signed_out(self, *_):
        self._abort_cnn()
        self._cnn_jobs.clear()
        self._cnn_requests.clear()
        self.store.abort()

    def _abort_cnn(self, *_):
        if self.cnn_worker is not None:
            self.cnn_worker.abort()

    def refresh_storage(self):
        if self.closing or self.settings_pending or not self.can_change():
            return False
        self.settings_pending = True
        self.loaded = False
        accepted = self._submit('GET', '/api/v1/settings/storage', kind='storage_get')
        if not accepted:
            self.settings_pending = False
        self.changed.emit()
        return bool(accepted)

    def _submit(self, method, path, body=None, kind='', camera_id=None):
        request_id = self.store.submit(method, path, body, kind, camera_id)
        if request_id is None:
            self._failed('이벤트 저장 큐가 가득 찼거나 종료되어 요청을 접수하지 못했습니다.')
        else:
            self._pending[request_id] = (kind, body)
        return request_id

    def set_root(self, root):
        if self.closing or self.settings_pending or not self.can_change():
            return False
        self.settings_pending = True
        accepted = self._submit('PUT', '/api/v1/settings/storage', {'storage_root': root}, 'storage_put')
        if not accepted:
            self.settings_pending = False
        self.changed.emit()
        return bool(accepted)

    def camera_event(self, kind, camera_id, execution_id, occurred_at):
        if kind == 'camera_started':
            self._active_executions.add((camera_id, execution_id.replace('-', '')))
            self.locked = True
            self.storage_changed.emit(self.root, True)
        body = dict(event_type=kind, camera_id=camera_id, camera_execution_id=execution_id,
                    occurred_at=occurred_at.isoformat())
        return self._submit('POST', '/api/v1/events', body, kind, camera_id)

    def person_update(self, update):
        execution = update.execution_id.replace('-', '')
        key = (update.camera_id, execution, update.tracking_generation, update.local_person_id)
        if self.closing or key in self._closed_persons or (update.camera_id, execution) not in self._active_executions:
            return
        if (type(update.global_person_id) is not int or update.global_person_id < 1 or update.crop_task_id is None
                or update.first_seen_utc is None or update.last_seen_utc is None
                or update.first_seen_utc.tzinfo is None or update.last_seen_utc.tzinfo is None):
            return
        state = self._persons.get(key)
        if state is not None and (update.revision <= state.revision or update.crop_task_id != state.task_id
                                  or update.global_person_id != state.identity['global_person_id']
                                  or update.first_seen_utc != state.first_seen):
            return
        media = {}
        for name in MEDIA:
            path = getattr(update, name)
            try:
                media[name] = Path(path).relative_to(Path(self.root)).as_posix() if path is not None else None
            except ValueError:
                media[name] = None  # temp/다른 저장소에 남은 파일은 등록하지 않는다.
        if state is None:
            if len(self._persons) >= 128:
                self._failed('인물 이벤트 저장이 지연되어 일부 관측을 접수하지 못했습니다.')
                return
            identity = dict(camera_id=update.camera_id, camera_execution_id=update.execution_id,
                            tracking_generation=update.tracking_generation, local_person_id=update.local_person_id,
                            global_person_id=update.global_person_id)
            state = PersonEvent(identity, update.crop_task_id, update.first_seen_utc, update.last_seen_utc,
                                update.revision, media)
            self._persons[key] = state
        state.revision, state.media, state.last_seen = update.revision, media, update.last_seen_utc
        source = getattr(update, 'cnn_source_path', None)
        if source is not None and state.cnn_source_path is None:
            state.cnn_source_path = source
        state.finished = update.finished_at is not None
        state.natural = state.finished and update.finish_reason == 'natural'
        self._pump_person(key)

    def end_execution(self, camera_id, execution_id):
        execution = execution_id.replace('-', '')
        self._active_executions.discard((camera_id, execution))
        for key, state in list(self._persons.items()):
            if key[:2] == (camera_id, execution):
                state.finished = True  # 마지막 미디어 알림 실패도 자연 유실로 바꾸지 않는다.
                self._pump_person(key)

    def _complete_person(self, key):
        state = self._persons[key]
        if state.finished and state.inflight is None and (state.failed or (
                state.event_id is not None and (not state.natural or state.disappeared)
                and (state.saved_media == state.media or state.failed_media_revision >= state.revision))):
            del self._persons[key]
            self._closed_persons[key] = state.task_id
            if len(self._closed_persons) > 256:
                self._closed_persons.popitem(last=False)

    def _pump_person(self, key):
        state = self._persons[key]
        self._try_cnn(state)
        if state.inflight is not None:
            return
        if state.failed:
            self._complete_person(key)
            return
        if state.event_id is None:
            operation = 'appear'
            media = dict.fromkeys(MEDIA) if state.create_without_media else state.media
            body = state.identity | dict(event_type='person_appeared', occurred_at=state.first_seen.isoformat()) | media
            method, path = 'POST', '/api/v1/events'
        elif state.saved_media != state.media and state.revision > state.failed_media_revision:
            # 개별 완료의 옛 요청이 나중에 실행돼도 다른 파일의 새 경로를 NULL로 지우지 않는다.
            operation, body = 'media', {name: value for name, value in state.media.items() if value is not None}
            method, path = 'PATCH', f'/api/v1/events/{state.event_id}/media'
        elif state.finished and state.natural and not state.disappeared:
            operation = 'disappear'
            body = state.identity | dict(event_type='person_disappeared', occurred_at=state.last_seen.isoformat(),
                                         related_event_id=state.event_id)
            method, path = 'POST', '/api/v1/events'
        else:
            self._complete_person(key)
            return
        request_id = self._submit(method, path, body, 'person_' + operation, state.identity['camera_id'])
        if request_id is None:
            state.failed = True
            self._complete_person(key)
        else:
            state.inflight = request_id
            self._person_requests[request_id] = (key, operation, state.revision)

    def _try_cnn(self, state):
        if (state.cnn_attempted or state.failed or state.event_id is None
                or state.cnn_source_path is None or self._shutdown_expired):
            return
        state.cnn_attempted = True
        # HTTP가 느려도 분석/저장 대기 상태가 무한히 늘어나지 않게 한다.
        if len(self._cnn_jobs) >= 64:
            self._failed('CNN 대기 작업이 가득 차 일부 인상착의 분석을 제외했습니다.')
            return
        if self.cnn_worker is None:
            self.cnn_worker = self._cnn_factory()
            self.destroyed.connect(self.cnn_worker.abort)
            self.cnn_worker.start()
        if not self.cnn_worker.submit(state.event_id, state.cnn_source_path):
            self._failed('CNN 작업을 접수하지 못했습니다. 모델 또는 분석 큐를 확인하세요.')
            return
        self._cnn_jobs[state.event_id] = dict(state.identity, event_id=state.event_id,
                                             server_run_id=state.server_run_id,
                                             occurred_at=state.first_seen.isoformat())

    def _poll_cnn(self):
        if self.cnn_worker is None:
            return
        for result in self.cnn_worker.take_results():
            expected = self._cnn_jobs.get(result['event_id'])
            if expected is None or self._shutdown_expired:
                continue
            if result['error']:
                del self._cnn_jobs[result['event_id']]
                self._failed('일부 인상착의 CNN 분석에 실패했습니다. 등장 이벤트는 유지됩니다.')
                continue
            body = {'cnn_result': result['result']}
            request_id = self._submit('PATCH', f"/api/v1/events/{result['event_id']}/cnn-result",
                                      body, 'cnn_result', expected['camera_id'])
            if request_id is None:
                del self._cnn_jobs[result['event_id']]
            else:
                self._cnn_requests[request_id] = result['event_id']

    def _cnn_result(self, result, body):
        event_id = self._cnn_requests.pop(result['request_id'])
        expected = self._cnn_jobs.pop(event_id, None)
        payload, error = result['payload'], result['error']
        try:
            valid = (expected is not None and isinstance(payload, dict)
                     and type(payload.get('event_id')) is int
                     and payload['event_type'] == 'person_appeared'
                     and all(payload[name] == value for name, value in expected.items()
                             if name not in ('camera_execution_id', 'occurred_at'))
                     and payload['camera_execution_id'].replace('-', '') == expected['camera_execution_id'].replace('-', '')
                     and datetime.fromisoformat(payload['occurred_at']) == datetime.fromisoformat(expected['occurred_at'])
                     and payload['cnn_result'] == body['cnn_result'])
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if error or not valid:
            self._failed('일부 CNN 결과 저장을 확인하지 못했습니다. 이벤트 이력을 확인하세요.')
        # CNN 결과 PATCH는 새 등장/타임라인 카드가 아니다.

    @staticmethod
    def _valid_person_response(payload, state, operation, body):
        try:
            expected = state.identity
            return (type(payload['event_id']) is int and payload['event_id'] > 0
                    and type(payload['server_run_id']) is int and payload['server_run_id'] > 0
                    and all(type(payload[name]) is int for name in ('camera_id', 'tracking_generation', 'local_person_id', 'global_person_id'))
                    and all(name in payload for name in MEDIA)
                    and all(payload[name] == expected[name] for name in expected if name != 'camera_execution_id')
                    and payload['camera_execution_id'].replace('-', '') == expected['camera_execution_id'].replace('-', '')
                    and payload['event_type'] == ('person_disappeared' if operation == 'disappear' else 'person_appeared')
                    and datetime.fromisoformat(payload['occurred_at']) == (datetime.fromisoformat(body['occurred_at']) if operation == 'disappear' else state.first_seen)
                    and (operation != 'media' or (payload['event_id'] == state.event_id
                         and all(payload[name] == body[name] for name in body)))
                    and (operation != 'disappear' or payload['related_event_id'] == state.event_id))
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    def _person_result(self, result, body):
        key, operation, revision = self._person_requests.pop(result['request_id'])
        state = self._persons.get(key)
        if state is None or state.inflight != result['request_id']:
            return
        state.inflight = None
        payload, error = result['payload'], result['error']
        if not error and not self._valid_person_response(payload, state, operation, body):
            error = 'INVALID_RESPONSE'
        if error:
            if operation == 'appear' and error == 'REQUEST_REJECTED' and not state.create_without_media and any(body.get(f) for f in MEDIA):
                # 폴더 rename과 POST가 경합하면 불변 식별값을 유지하고 미디어 없이 한 번만 생성 재확인한다.
                state.create_without_media = True
                self._pump_person(key)
                return
            if operation == 'media':
                state.failed_media_revision = revision
                if state.revision > revision:
                    self._pump_person(key)
                    return
            else:
                state.failed = True
            self._failed('일부 인물 이벤트 또는 미디어 경로의 저장을 확인하지 못했습니다.')
        elif operation == 'appear':
            state.event_id = payload['event_id']
            state.server_run_id = payload['server_run_id']
            state.saved_media = {name: payload.get(name) for name in MEDIA}
        elif operation == 'media':
            state.saved_media = {name: payload[name] for name in MEDIA}
        else:
            state.disappeared = True
        if not error and operation != 'media':
            self.event_saved.emit(dict(payload))
        self._pump_person(key)

    def _failed(self, message):
        self.failures += 1
        self.notice.emit(message)

    def poll(self):
        if self.finished:
            return
        self._poll_cnn()
        for result in self.store.take_results():
            pending = self._pending.pop(result['request_id'], None)
            if pending is None:
                continue
            kind, body = pending
            if result['request_id'] in self._cnn_requests:
                self._cnn_result(result, body)
                continue
            if result['request_id'] in self._person_requests:
                self._person_result(result, body)
                self.changed.emit()
                continue
            payload, error = result['payload'], result['error']
            if not error and kind.startswith('storage_'):
                if (not isinstance(payload.get('storage_root'), (str, type(None)))
                        or type(payload.get('locked')) is not bool
                        or (payload['storage_root'] is not None and not Path(payload['storage_root']).is_absolute())):
                    error = 'INVALID_RESPONSE'
                else:
                    self.root = payload['storage_root'] or ''
                    self.locked = payload['locked']
                    self.loaded = True
                    self.storage_changed.emit(self.root, self.locked)
            elif not error:
                try:
                    valid = (type(payload.get('event_id')) is int and payload['event_id'] > 0
                             and type(payload.get('server_run_id')) is int and payload['server_run_id'] > 0
                             and all(payload.get(f) == body[f] for f in ('camera_id', 'event_type'))
                             and payload['camera_execution_id'].replace('-', '') == body['camera_execution_id'].replace('-', '')
                             and datetime.fromisoformat(payload['occurred_at']) == datetime.fromisoformat(body['occurred_at']))
                except (KeyError, AttributeError, TypeError, ValueError):
                    valid = False
                if not valid:
                    error = 'INVALID_RESPONSE'
            if error:
                self._failed('이벤트 저장 또는 저장소 설정을 확인하지 못했습니다. 설정·서버 연결을 확인하세요.')
            elif kind in ('camera_started', 'camera_stopped'):
                self.event_saved.emit(dict(payload))
            if kind.startswith('storage_'):
                self.settings_pending = False
            if kind == 'storage_put':
                self.settings_finished.emit(not error, '서버 저장소 설정을 확인하지 못했습니다.' if error else '')
            self.changed.emit()
        cnn_alive = self.cnn_worker is not None and self.cnn_worker.is_alive()
        if self.closing and not self._pending and self.cnn_worker is not None:
            self.cnn_worker.finish()
        if self.closing and not self._pending and not cnn_alive and not self._cnn_jobs:
            self.store.finish(max(0, self._deadline - time.monotonic()))
        if self.closing and time.monotonic() >= self._deadline and not self._shutdown_expired:
            self._shutdown_expired = True
            if self._pending or self._cnn_jobs or cnn_alive:
                self._failed('종료 제한 시간 안에 일부 이벤트/CNN 저장을 확인하지 못했습니다. 실행 중인 CNN 정리를 기다립니다.')
            self._abort_cnn()
            self.store.abort()
            self._pending.clear()
            self._cnn_requests.clear()
            self._cnn_jobs.clear()
        if self.closing and not cnn_alive and (not self.store.is_alive() or self._shutdown_expired):
            self.store.abort()
            self._pending.clear()
            self._person_requests.clear()
            self._persons.clear()
            self._active_executions.clear()
            self.finished = True
            self.timer.stop()
            warning = '일부 이벤트 또는 CNN 결과 저장을 확인하지 못했습니다. 서버의 이벤트 이력을 확인하세요.' if self.failures else ''
            self.drained.emit(warning)

    def begin_shutdown(self, timeout=8):
        if not self.closing:
            self.closing = True
            self._deadline = time.monotonic() + timeout
        self.poll()
