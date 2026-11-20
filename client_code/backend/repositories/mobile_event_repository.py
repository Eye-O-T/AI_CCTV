"""날짜별 카드와 표시용 등장 정보를 한 SELECT로 조회한다."""
from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import aliased

from ..models.camera import Camera
from ..models.event import Event


class MobileEventRepository:
    def get(self, db, event_id):
        return db.get(Event, event_id)

    def list(self, db, query, start, end):
        source = aliased(Event)
        source_id = case((Event.event_type == 'person_disappeared', Event.related_event_id),
                         else_=Event.event_id)
        statement = (select(Event, Camera.name, source.crop_path, source.cnn_result)
                     .join(Camera, Camera.camera_id == Event.camera_id)
                     .outerjoin(source, source.event_id == source_id)
                     .where(Event.occurred_at >= start, Event.occurred_at < end))
        if query.before_occurred_at is not None:
            statement = statement.where(or_(
                Event.occurred_at < query.before_occurred_at,
                and_(Event.occurred_at == query.before_occurred_at, Event.event_id < query.before_event_id)))
        return db.execute(statement.order_by(Event.occurred_at.desc(), Event.event_id.desc())
                          .limit(query.limit + 1)).all()
