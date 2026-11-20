"""기존 관리자 Access 인증을 재사용하는 모바일 목록·crop API."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from ..dependencies import get_mobile_event_service, require_user_id
from ..schemas.auth import ErrorResponse
from ..schemas.mobile_event import MobileEventListParameters, MobileEventListResponse
from ..services.mobile_event_service import MobileEventService


router = APIRouter(prefix='/api/v1/mobile/events', tags=['모바일 이벤트'],
                   dependencies=[Depends(require_user_id)],
                   responses={code: {'model': ErrorResponse} for code in (401, 422, 500)})


@router.get('', response_model=MobileEventListResponse)
def list_mobile_events(query: Annotated[MobileEventListParameters, Query()],
                       service: Annotated[MobileEventService, Depends(get_mobile_event_service)],
                       response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.list(query)


@router.get('/{event_id}/crop', response_class=Response, responses={
    200: {'content': {'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}},
    404: {'model': ErrorResponse}})
def get_mobile_event_crop(event_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
                          service: Annotated[MobileEventService, Depends(get_mobile_event_service)]):
    return Response(content=service.crop(event_id), media_type='image/jpeg',
                    headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
