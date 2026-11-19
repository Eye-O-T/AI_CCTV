"""FastAPI 앱과 인증 백엔드의 시작·종료 진입점."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .database import Base, DEFAULT_DB_PATH, create_database, single_instance
from .errors import APIError
from .routers.auth import router
from .routers.cameras import router as camera_router
from .services.camera_service import CameraService
from .services.auth_service import AuthService
from .services.server_run_service import ServerRunService
from .routers.global_persons import router as global_person_router
from .services.global_person_service import GlobalPersonService
from .routers.events import router as event_router
from .routers.settings import router as settings_router
from .services.event_service import EventService
from .services.storage_service import StorageService


def create_app(db_path: Path | None = None, storage_config_path: Path | None = None) -> FastAPI:
    path = (db_path or DEFAULT_DB_PATH).resolve()
    config_path = (storage_config_path or path.with_suffix('.storage.json')).resolve()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        with single_instance(path):
            engine, factory = create_database(path)
            run_id = None
            auth = None
            global_persons = None
            runs = ServerRunService(factory)
            try:
                Base.metadata.create_all(engine)
                storage = StorageService(factory, config_path)
                run_id = runs.start()
                auth = AuthService(factory, run_id)
                application.state.auth_service = auth
                application.state.camera_service = CameraService(factory)
                global_persons = GlobalPersonService(factory)
                application.state.global_person_service = global_persons
                application.state.storage_service = storage
                application.state.event_service = EventService(factory, run_id, storage)
                yield
            finally:
                if global_persons is not None:
                    global_persons.close()
                if auth is not None:
                    auth.close()
                try:
                    if run_id is not None:
                        runs.stop(run_id)
                finally:
                    engine.dispose()

    application = FastAPI(title="AI CCTV Backend", lifespan=lifespan)

    @application.exception_handler(APIError)
    async def api_error(_: Request, exc: APIError):
        return JSONResponse(status_code=exc.status, content=exc.body())

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # 오류 input/ctx에는 비밀번호가 포함될 수 있어 반환하지 않는다.
        location = exc.errors()[0].get("loc", ()) if exc.errors() else ()
        allowed = {"username", "password", "password_confirm", "refresh_token",
                   "camera_id", "name", "camera_type", "webcam_index", "rtsp_url",
                   "edge_api_base_url", "is_enabled", "created_at", "updated_at",
                   "global_person_id", "request_id", "feature_vector", "observed_at",
                   "event_id", "event_type", "server_run_id", "camera_execution_id",
                   "tracking_generation", "local_person_id", "related_event_id", "occurred_at",
                   "crop_path", "clip_path", "trajectory_path", "storage_root",
                   "limit", "before_occurred_at", "before_event_id"}
        field = next((item for item in location if isinstance(item, str) and item in allowed), None)
        error = APIError(422, "VALIDATION_ERROR", "요청 입력을 확인해 주세요.", field)
        return JSONResponse(status_code=422, content=error.body())

    @application.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception):
        error = APIError(500, "INTERNAL_SERVER_ERROR", "서버 내부 오류가 발생했습니다.")
        return JSONResponse(status_code=500, content=error.body())

    application.include_router(router)
    application.include_router(camera_router)
    application.include_router(global_person_router)
    application.include_router(event_router)
    application.include_router(settings_router)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application


app = create_app()
