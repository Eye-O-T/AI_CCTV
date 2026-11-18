"""최초 대표 벡터를 보존하는 글로벌 인물."""
from datetime import datetime

from sqlalchemy import CheckConstraint, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base, UTCDateTime, utc_now


def _initial_updated_at(context):
    return context.get_current_parameters()['created_at']


class GlobalPerson(Base):
    __tablename__ = 'global_person_manage'
    __table_args__ = (
        CheckConstraint("typeof(feature_vector) = 'blob' AND length(feature_vector) = 2048",
                        name='ck_global_person_vector'),
        {'sqlite_autoincrement': True},
    )

    global_person_id: Mapped[int] = mapped_column(primary_key=True)
    feature_vector: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_initial_updated_at)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
