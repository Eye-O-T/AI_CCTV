"""글로벌 인물 트랜잭션과 단일 서버 실행의 등록 재시도 기록."""
from threading import RLock

from sqlalchemy import text

from ...feature_vector import decode_vector, encode_vector
from ..errors import APIError
from ..repositories.global_person_repository import GlobalPersonRepository
from ..schemas.global_person import GlobalPersonResponse, GlobalPersonListResponse


class GlobalPersonService:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.repository = GlobalPersonRepository()
        self._lock = RLock()
        self._requests = {}

    def close(self):
        with self._lock:
            self._requests.clear()

    def _response(self, person):
        return GlobalPersonResponse(
            global_person_id=person.global_person_id,
            feature_vector=decode_vector(person.feature_vector),
            created_at=person.created_at, updated_at=person.updated_at,
            last_seen_at=person.last_seen_at,
        )

    def _get(self, db, person_id):
        person = self.repository.get(db, person_id)
        if person is None:
            raise APIError(404, 'GLOBAL_PERSON_NOT_FOUND', '등록되지 않은 글로벌 인물입니다.', 'global_person_id')
        return person

    def list(self):
        with self.session_factory() as db:
            return GlobalPersonListResponse(items=[self._response(row) for row in self.repository.list(db)])

    def create(self, user_id, body):
        key = (user_id, body.request_id)
        vector = encode_vector(body.feature_vector)
        fingerprint = (vector, body.observed_at)
        with self._lock:
            previous = self._requests.get(key)
            with self.session_factory() as db:
                if previous is not None:
                    if previous[:2] != fingerprint:
                        raise APIError(409, 'GLOBAL_PERSON_REQUEST_CONFLICT',
                                       '같은 요청 ID의 등록 내용이 다릅니다.', 'request_id')
                    return self._response(self._get(db, previous[2])), False
                try:
                    person = self.repository.add(db, vector, body.observed_at)
                    response = self._response(person)
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                self._requests[key] = (*fingerprint, response.global_person_id)
                return response, True

    def update_last_seen(self, person_id, body):
        with self.session_factory() as db:
            try:
                # SELECT 이전 쓰기 예약으로 서로 다른 요청의 max 갱신을 보장한다.
                db.execute(text('BEGIN IMMEDIATE'))
                person = self._get(db, person_id)
                self.repository.update_last_seen(db, person, body.observed_at)
                response = self._response(person)
                db.commit()
                return response
            except Exception:
                db.rollback()
                raise
