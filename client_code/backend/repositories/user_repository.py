from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User


class UserRepository:
    def exists(self, db: Session) -> bool:
        return db.scalar(select(User.user_id).limit(1)) is not None

    def find_by_username(self, db: Session, username: str) -> User | None:
        return db.scalar(select(User).where(User.username == username))

    def add(self, db: Session, username: str, password_hash: str) -> User:
        user = User(username=username, password_hash=password_hash)
        db.add(user)
        db.flush()
        return user
