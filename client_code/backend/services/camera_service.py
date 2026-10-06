"""카메라 등록·수정 트랜잭션과 최종 입력 검증."""
import sqlite3

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..errors import APIError
from ..repositories.camera_repository import CameraRepository
from ..schemas.camera import CameraCreate, CameraUpdate, CameraSummary, CameraResponse, CameraListResponse


def _source_conflict(exc: IntegrityError) -> APIError | None:
    # SQLite의 단일 컬럼 UNIQUE 위반만 계약 오류로 변환한다.
    if getattr(exc.orig, "sqlite_errorcode", None) != sqlite3.SQLITE_CONSTRAINT_UNIQUE:
        return None
    for field in ("webcam_index", "rtsp_url"):
        if str(exc.orig) == f"UNIQUE constraint failed: cameras.{field}":
            return APIError(409, "CAMERA_SOURCE_CONFLICT", "이미 등록된 카메라 입력입니다.", field)
    return None


class CameraService:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.repository = CameraRepository()

    def list(self, is_enabled: bool | None = None) -> CameraListResponse:
        with self.session_factory() as db:
            return CameraListResponse(items=[CameraSummary.model_validate(row)
                for row in self.repository.list(db, is_enabled)])

    def get(self, camera_id: int) -> CameraResponse:
        with self.session_factory() as db:
            return CameraResponse.model_validate(self._get(db, camera_id))

    def _get(self, db, camera_id):
        camera = self.repository.get(db, camera_id)
        if camera is None:
            raise APIError(404, "CAMERA_NOT_FOUND", "등록되지 않은 카메라입니다.", "camera_id")
        return camera

    def create(self, body: CameraCreate) -> CameraResponse:
        with self.session_factory() as db:
            try:
                camera = self.repository.add(db, body.model_dump())
                response = CameraResponse.model_validate(camera)
                db.commit()
                return response
            except IntegrityError as exc:
                db.rollback()
                conflict = _source_conflict(exc)
                if conflict is not None:
                    raise conflict from None
                raise
            except Exception:
                db.rollback()
                raise

    def update(self, camera_id: int, body: CameraUpdate) -> CameraResponse:
        with self.session_factory() as db:
            try:
                # 조회 전에 쓰기 예약: 동시 PATCH가 오래된 값을 병합하지 않도록 한다.
                db.execute(text("BEGIN IMMEDIATE"))
                camera = self._get(db, camera_id)
                changes = body.model_dump(exclude_unset=True)
                existing = {key: getattr(camera, key) for key in CameraCreate.model_fields}
                try:
                    validated = CameraCreate.model_validate(existing | changes)
                except ValidationError:
                    raise APIError(422, "VALIDATION_ERROR", "카메라 종류에 맞는 입력을 확인해 주세요.") from None
                values = validated.model_dump()
                self.repository.update(db, camera, {key: values[key] for key in changes})
                response = CameraResponse.model_validate(camera)
                db.commit()
                return response
            except IntegrityError as exc:
                db.rollback()
                conflict = _source_conflict(exc)
                if conflict is not None:
                    raise conflict from None
                raise
            except Exception:
                db.rollback()
                raise
