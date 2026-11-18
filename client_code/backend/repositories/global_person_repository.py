"""글로벌 인물 조회/flush. commit은 Service 책임이다."""
from sqlalchemy import select

from ..models.global_person import GlobalPerson


class GlobalPersonRepository:
    def list(self, db):
        return list(db.scalars(select(GlobalPerson).order_by(GlobalPerson.global_person_id)))

    def get(self, db, person_id):
        return db.get(GlobalPerson, person_id)

    def add(self, db, vector, observed_at):
        person = GlobalPerson(feature_vector=vector, last_seen_at=observed_at)
        db.add(person)
        db.flush()
        return person

    def update_last_seen(self, db, person, observed_at):
        if observed_at > person.last_seen_at:
            person.last_seen_at = observed_at
        db.flush()
