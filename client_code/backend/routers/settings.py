"""저장소 기준 경로만 관리하는 관리자 설정 API."""
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ..dependencies import get_storage_service, require_user_id
from ..schemas.auth import ErrorResponse
from ..schemas.storage import StorageResponse, StorageUpdate
from ..services.storage_service import StorageService


router = APIRouter(prefix='/api/v1/settings', tags=['저장소 설정'], dependencies=[Depends(require_user_id)],
                   responses={code: {'model': ErrorResponse} for code in (401, 409, 422, 500)})
Service = Annotated[StorageService, Depends(get_storage_service)]


@router.get('/storage', response_model=StorageResponse)
def get_storage(service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.get()


@router.put('/storage', response_model=StorageResponse)
def update_storage(body: StorageUpdate, service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.update(body)
