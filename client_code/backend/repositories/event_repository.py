"""이벤트 DB 접근. commit/rollback은 Service에서 수행한다."""
from sqlalchemy import and_, or_, select

from ..models.event import Event


class EventRepository:
    def get(self, db, event_id):
        return db.get(Event, event_id)

    def find_duplicate(self, db, values):
        fields = ['server_run_id', 'camera_id', 'camera_execution_id', 'event_type']
        if values['event_type'].startswith('person_'):
            fields += ['tracking_generation', 'local_person_id']
        return db.scalar(select(Event).where(*(getattr(Event, f) == values[f] for f in fields)))

    def add(self, db, values):
        row = Event(**values)
        db.add(row)
        db.flush()
        return row

    def update_media(self, db, row, changes):
        for field, value in changes.items():
            setattr(row, field, value)
        db.flush()

    def update_cnn_result(self, db, row, result):
        row.cnn_result = result
        db.flush()

    def list(self, db, query):
        statement = select(Event)
        if query.before_occurred_at is not None:
            statement = statement.where(or_(
                Event.occurred_at < query.before_occurred_at,
                and_(Event.occurred_at == query.before_occurred_at, Event.event_id < query.before_event_id)))
        return list(db.scalars(statement.order_by(Event.occurred_at.desc(), Event.event_id.desc()).limit(query.limit)))
