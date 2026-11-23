"""카메라 등록 정보. 실제 연결 상태는 저장하지 않는다."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base, UTCDateTime, utc_now


def _initial_updated_at(context):
    return context.get_current_parameters()["created_at"]


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 100", name="ck_camera_name"),
        CheckConstraint("camera_type IN ('webcam', 'edge_node')", name="ck_camera_type"),
        CheckConstraint(
            "webcam_index IS NULL OR (typeof(webcam_index) = 'integer' AND webcam_index >= 0)",
            name="ck_camera_webcam_index",
        ),
        CheckConstraint(
            "(camera_type = 'webcam' AND webcam_index IS NOT NULL "
            "AND rtsp_url IS NULL AND edge_api_base_url IS NULL) OR "
            "(camera_type = 'edge_node' AND webcam_index IS NULL "
            "AND rtsp_url IS NOT NULL AND length(trim(rtsp_url)) > 0)",
            name="ck_camera_source",
        ),
        {"sqlite_autoincrement": True},
    )

    camera_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    camera_type: Mapped[str] = mapped_column(Text)
    webcam_index: Mapped[int | None] = mapped_column(unique=True)
    rtsp_url: Mapped[str | None] = mapped_column(Text, unique=True)
    edge_api_base_url: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="ck_camera_enabled"), default=True, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=_initial_updated_at, onupdate=utc_now
    )
