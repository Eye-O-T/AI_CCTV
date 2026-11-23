from sqlalchemy import update
from sqlalchemy.orm import Session

from ..database import utc_now
from ..models import ServerRun


class ServerRunRepository:
    def interrupt_previous(self, db: Session) -> None:
        db.execute(
            update(ServerRun).where(ServerRun.status == "running").values(status="interrupted")
        )

    def add(self, db: Session) -> ServerRun:
        run = ServerRun(status="running")
        db.add(run)
        db.flush()
        return run

    def stop(self, db: Session, run_id: int) -> None:
        db.execute(
            update(ServerRun)
            .where(ServerRun.server_run_id == run_id, ServerRun.status == "running")
            .values(status="stopped", ended_at=utc_now())
        )
