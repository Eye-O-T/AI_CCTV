"""인증된 글로벌 인물 관리 HTTP API."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from ..dependencies import get_global_person_service, require_user_id
from ..schemas.auth import ErrorResponse
from ..schemas.global_person import (
    GlobalPersonCreate, GlobalPersonResponse, GlobalPersonListResponse, LastSeenRequest,
)
from ..services.global_person_service import GlobalPersonService

router = APIRouter(
    prefix='/api/v1/global-persons', tags=['글로벌 인물'],
    dependencies=[Depends(require_user_id)],
    responses={code: {'model': ErrorResponse} for code in (401, 422, 500)},
)
Service = Annotated[GlobalPersonService, Depends(get_global_person_service)]
UserId = Annotated[int, Depends(require_user_id)]
PersonId = Annotated[int, Path(ge=1)]


@router.get('', response_model=GlobalPersonListResponse)
def list_global_persons(service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.list()


@router.post('', status_code=201, response_model=GlobalPersonResponse,
             responses={200: {'model': GlobalPersonResponse}, 409: {'model': ErrorResponse}})
def create_global_person(body: GlobalPersonCreate, service: Service, user_id: UserId, response: Response):
    result, created = service.create(user_id, body)
    response.status_code = 201 if created else 200
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.patch('/{global_person_id}/last-seen', response_model=GlobalPersonResponse,
              responses={404: {'model': ErrorResponse}})
def update_last_seen(global_person_id: PersonId, body: LastSeenRequest, service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.update_last_seen(global_person_id, body)
