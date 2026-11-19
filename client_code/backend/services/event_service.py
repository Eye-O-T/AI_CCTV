"""이벤트 생성·조회·미디어 갱신 및 저장소 경계를 검증하는 트랜잭션."""
from sqlalchemy import text

from ..errors import APIError
from ..models.camera import Camera
from ..models.global_person import GlobalPerson
from ..models.server_run import ServerRun
from ..repositories.event_repository import EventRepository
from ..schemas.event import EventListQuery, EventListResponse, EventResponse, MEDIA_FIELDS


class EventService:
    def __init__(self, session_factory, server_run_id, storage):
        self.session_factory = session_factory
        self.server_run_id = server_run_id
        self.repository = EventRepository()
        self.storage = storage

    def _get(self, db, event_id):
        row = self.repository.get(db, event_id)
        if row is None:
            raise APIError(404, 'EVENT_NOT_FOUND', '등록되지 않은 이벤트입니다.', 'event_id')
        return row

    def create(self, body):
        values = body.model_dump()
        values['camera_execution_id'] = str(body.camera_execution_id)
        values['server_run_id'] = self.server_run_id
        with self.storage.lock, self.session_factory() as db:
            try:
                # 조회·삽입 전체를 SQLite 쓰기 예약으로 보호한다. 여러 Service 인스턴스에도 유효하다.
                db.execute(text('BEGIN IMMEDIATE'))
                run = db.get(ServerRun, self.server_run_id)
                if run is None or run.status != 'running':
                    raise APIError(409, 'EVENT_RUN_CONFLICT', '현재 서버 실행을 확인할 수 없습니다.')
                self.storage.require_root()
                previous = self.repository.find_duplicate(db, values)
                if previous is not None:
                    immutable = set(values) - set(MEDIA_FIELDS)
                    if any(getattr(previous, field) != values[field] for field in immutable):
                        raise APIError(409, 'EVENT_REQUEST_CONFLICT', '같은 이벤트 식별값의 내용이 다릅니다.')
                    response = EventResponse.model_validate(previous)
                    db.commit()
                    return response, False
                if db.get(Camera, body.camera_id) is None:
                    raise APIError(404, 'CAMERA_NOT_FOUND', '등록되지 않은 카메라입니다.', 'camera_id')
                if body.global_person_id is not None and db.get(GlobalPerson, body.global_person_id) is None:
                    raise APIError(404, 'GLOBAL_PERSON_NOT_FOUND', '등록되지 않은 글로벌 인물입니다.', 'global_person_id')
                if body.related_event_id is not None:
                    appeared = self._get(db, body.related_event_id)
                    fields = ('server_run_id', 'camera_id', 'camera_execution_id',
                              'tracking_generation', 'local_person_id', 'global_person_id')
                    if (appeared.event_type != 'person_appeared'
                            or any(getattr(appeared, f) != values[f] for f in fields)
                            or body.occurred_at < appeared.occurred_at):
                        raise APIError(409, 'EVENT_RELATION_CONFLICT', '같은 관측의 등장 이벤트와 시각을 확인하세요.', 'related_event_id')
                self.storage.validate_media(values)
                row = self.repository.add(db, values)
                response = EventResponse.model_validate(row)
                db.commit()
                return response, True
            except Exception:
                db.rollback()
                raise

    def update_media(self, event_id, body):
        with self.storage.lock, self.session_factory() as db:
            try:
                db.execute(text('BEGIN IMMEDIATE'))
                row = self._get(db, event_id)
                if row.event_type != 'person_appeared':
                    raise APIError(409, 'EVENT_MEDIA_NOT_ALLOWED', '등장 이벤트의 미디어만 갱신할 수 있습니다.')
                changes = body.model_dump(exclude_unset=True)
                media = {field: getattr(row, field) for field in MEDIA_FIELDS} | changes
                self.storage.validate_media(media | {'camera_id': row.camera_id, 'global_person_id': row.global_person_id})
                self.repository.update_media(db, row, changes)
                response = EventResponse.model_validate(row)
                db.commit()
                return response
            except Exception:
                db.rollback()
                raise

    def get(self, event_id):
        with self.session_factory() as db:
            return EventResponse.model_validate(self._get(db, event_id))

    def list(self, query: EventListQuery | None = None):
        with self.session_factory() as db:
            return EventListResponse(items=[EventResponse.model_validate(row)
                for row in self.repository.list(db, query or EventListQuery())])
