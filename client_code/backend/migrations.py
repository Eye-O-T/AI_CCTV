"""기존 이벤트를 보존하는 CNN 결과 컬럼 전용 SQLite 마이그레이션."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4


def migrate_event_cnn_result(db_path: Path):
    """단일 인스턴스 잠금 안에서 호출한다. 기존 DB만 백업 후 컬럼을 추가한다."""
    db_path = Path(db_path).resolve()
    if not db_path.exists():
        return None
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=rw', uri=True)) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(events)')}
        if not columns or 'cnn_result' in columns:
            return None
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = db_path.with_name(f'{db_path.name}.before-cnn-result-{stamp}-{uuid4().hex[:8]}.bak')
        # sqlite backup API는 WAL의 커밋된 내용도 포함한다. 백업 실패 시 ALTER하지 않는다.
        with closing(sqlite3.connect(backup)) as destination:
            db.backup(destination)
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute("ALTER TABLE events ADD COLUMN cnn_result JSON "
                       "CONSTRAINT ck_event_cnn_result CHECK (cnn_result IS NULL OR "
                       "(event_type = 'person_appeared' AND json_valid(cnn_result) "
                       "AND json_type(cnn_result) = 'object'))")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return backup
