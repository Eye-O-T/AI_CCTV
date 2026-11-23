from ..repositories.server_run_repository import ServerRunRepository


class ServerRunService:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.repository = ServerRunRepository()

    def start(self) -> int:
        # 호출자는 프로세스 잠금을 획득한 상태여야 한다.
        with self.session_factory.begin() as db:
            self.repository.interrupt_previous(db)
            return self.repository.add(db).server_run_id

    def stop(self, run_id: int) -> None:
        with self.session_factory.begin() as db:
            self.repository.stop(db, run_id)
