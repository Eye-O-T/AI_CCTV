"""카메라 DB 접근. commit과 트랜잭션은 Service에서 관리한다."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.camera import Camera


class CameraRepository:
    def get(self, db: Session, camera_id: int) -> Camera | None:
        return db.get(Camera, camera_id)

    def list(self, db: Session, is_enabled: bool | None) -> list[Camera]:
        query = select(Camera).order_by(Camera.camera_id)
        if is_enabled is not None:
            query = query.where(Camera.is_enabled == is_enabled)
        return list(db.scalars(query))

    def add(self, db: Session, values: dict) -> Camera:
        camera = Camera(**values)
        db.add(camera)
        db.flush()
        return camera

    def update(self, db: Session, camera: Camera, values: dict) -> None:
        for field, value in values.items():
            if getattr(camera, field) != value:
                setattr(camera, field, value)
        db.flush()
