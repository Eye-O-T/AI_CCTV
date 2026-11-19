"""기존 관리자 Access 인증을 사용하는 이벤트 JSON API."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from ..dependencies import get_event_service, require_user_id
from ..schemas.auth import ErrorResponse
from ..schemas.event import EventCreate, EventListParameters, EventListResponse, EventMediaUpdate, EventResponse
from ..services.event_service import EventService


router = APIRouter(prefix='/api/v1/events', tags=['이벤트'], dependencies=[Depends(require_user_id)],
                   responses={code: {'model': ErrorResponse} for code in (401, 404, 409, 422, 500)})
Service = Annotated[EventService, Depends(get_event_service)]
EventId = Annotated[int, Path(ge=1, le=2**63 - 1)]


@router.post('', status_code=201, response_model=EventResponse, responses={200: {'model': EventResponse}})
def create_event(body: EventCreate, service: Service, response: Response):
    result, created = service.create(body)
    response.status_code = 201 if created else 200
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Location'] = f'/api/v1/events/{result.event_id}'
    return result


@router.patch('/{event_id}/media', response_model=EventResponse)
def update_media(event_id: EventId, body: EventMediaUpdate, service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.update_media(event_id, body)


@router.get('', response_model=EventListResponse)
def list_events(query: Annotated[EventListParameters, Query()], service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.list(query)


@router.get('/{event_id}', response_model=EventResponse)
def get_event(event_id: EventId, service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.get(event_id)
