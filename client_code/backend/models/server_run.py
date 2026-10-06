"""백엔드 프로세스 실행 이력."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base, UTCDateTime, utc_now


class ServerRun(Base):
    __tablename__ = "server_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'stopped', 'interrupted')"),
        {"sqlite_autoincrement": True},
    )

    server_run_id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="running")
