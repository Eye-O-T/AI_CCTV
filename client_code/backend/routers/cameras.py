"""인증된 관리자의 카메라 관리 HTTP API."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response

from ..dependencies import get_camera_service, require_user_id
from ..schemas.auth import ErrorResponse
from ..schemas.camera import CameraCreate, CameraUpdate, CameraResponse, CameraListResponse
from ..services.camera_service import CameraService

router = APIRouter(
    prefix="/api/v1/cameras", tags=["카메라"],
    dependencies=[Depends(require_user_id)],
    responses={code: {"model": ErrorResponse} for code in (401, 422, 500)},
)
Service = Annotated[CameraService, Depends(get_camera_service)]
CameraId = Annotated[int, Path(ge=1)]


@router.post("", status_code=201, response_model=CameraResponse,
             responses={409: {"model": ErrorResponse}})
def create_camera(body: CameraCreate, service: Service, response: Response):
    camera = service.create(body)
    response.headers["Location"] = f"/api/v1/cameras/{camera.camera_id}"
    return camera


@router.get("", response_model=CameraListResponse)
def list_cameras(service: Service,
                 is_enabled: Annotated[Literal["true", "false"] | None, Query()] = None):
    return service.list(None if is_enabled is None else is_enabled == "true")


@router.get("/{camera_id}", response_model=CameraResponse,
            responses={404: {"model": ErrorResponse}})
def get_camera(camera_id: CameraId, service: Service):
    return service.get(camera_id)


@router.patch("/{camera_id}", response_model=CameraResponse,
              responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def update_camera(camera_id: CameraId, body: CameraUpdate, service: Service):
    return service.update(camera_id, body)
