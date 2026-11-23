"""실행 디렉터리에 독립적인 SQLite 연결과 단일 인스턴스 보호."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os

from sqlalchemy import Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "ai_cctv.db"


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """UTC ISO 8601(고정 소수점 6자리) 저장, aware datetime 반환."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timezone-aware datetime required")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def process_result_value(self, value, dialect):
        return datetime.fromisoformat(value) if value is not None else None


def create_database(path: Path):
    engine = create_engine(
        "sqlite:///" + path.as_posix(),
        connect_args={"check_same_thread": False, "timeout": 10},
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine, sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def single_instance(db_path: Path):
    """같은 DB를 쓰는 두 백엔드가 실행 이력·메모리 세션을 분리하지 못하게 한다."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with db_path.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Backend already running for this database; use one worker.") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
