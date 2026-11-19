"""이벤트 이력. 파일 작성·이동과 글로벌 매칭은 PC 작업의 책임이다."""
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base, UTCDateTime


class Event(Base):
    __tablename__ = 'events'
    __table_args__ = (
        CheckConstraint("event_type IN ('person_appeared', 'person_disappeared', 'camera_started', 'camera_stopped')",
                        name='ck_event_type'),
        CheckConstraint('length(camera_execution_id) = 36', name='ck_event_execution'),
        CheckConstraint(
            "(event_type IN ('person_appeared', 'person_disappeared') "
            "AND tracking_generation IS NOT NULL AND typeof(tracking_generation) = 'integer' "
            "AND tracking_generation >= 0 AND local_person_id IS NOT NULL "
            "AND typeof(local_person_id) = 'integer' AND local_person_id > 0 "
            "AND global_person_id IS NOT NULL) OR "
            "(event_type IN ('camera_started', 'camera_stopped') "
            "AND tracking_generation IS NULL AND local_person_id IS NULL AND global_person_id IS NULL)",
            name='ck_event_person_fields'),
        CheckConstraint(
            "(event_type = 'person_disappeared' AND related_event_id IS NOT NULL "
            "AND related_event_id != event_id) OR "
            "(event_type != 'person_disappeared' AND related_event_id IS NULL)",
            name='ck_event_related'),
        CheckConstraint(
            "event_type = 'person_appeared' OR "
            "(crop_path IS NULL AND clip_path IS NULL AND trajectory_path IS NULL)",
            name='ck_event_media'),
        Index('uq_event_person', 'server_run_id', 'camera_id', 'camera_execution_id',
              'tracking_generation', 'local_person_id', 'event_type', unique=True,
              sqlite_where=text("event_type IN ('person_appeared', 'person_disappeared')")),
        Index('uq_event_camera', 'server_run_id', 'camera_id', 'camera_execution_id',
              'event_type', unique=True,
              sqlite_where=text("event_type IN ('camera_started', 'camera_stopped')")),
        Index('ix_event_occurred_id', 'occurred_at', 'event_id'),
        {'sqlite_autoincrement': True},
    )

    event_id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    server_run_id: Mapped[int] = mapped_column(ForeignKey('server_runs.server_run_id', ondelete='RESTRICT'))
    camera_id: Mapped[int] = mapped_column(ForeignKey('cameras.camera_id', ondelete='RESTRICT'))
    camera_execution_id: Mapped[str] = mapped_column(Text)
    tracking_generation: Mapped[int | None]
    local_person_id: Mapped[int | None]
    global_person_id: Mapped[int | None] = mapped_column(
        ForeignKey('global_person_manage.global_person_id', ondelete='RESTRICT'))
    related_event_id: Mapped[int | None] = mapped_column(ForeignKey('events.event_id', ondelete='RESTRICT'))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    crop_path: Mapped[str | None] = mapped_column(Text)
    clip_path: Mapped[str | None] = mapped_column(Text)
    trajectory_path: Mapped[str | None] = mapped_column(Text)
