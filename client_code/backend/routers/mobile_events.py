"""기존 관리자 Access 인증을 재사용하는 모바일 목록·상세·미디어 API."""
from typing import Annotated
import re

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ..dependencies import get_mobile_event_service, require_user_id
from ..errors import APIError
from ..schemas.auth import ErrorResponse
from ..schemas.mobile_event import MobileEventDetail, MobileEventListParameters, MobileEventListResponse
from ..services.mobile_event_service import MobileEventService


router = APIRouter(prefix='/api/v1/mobile/events', tags=['모바일 이벤트'],
                   dependencies=[Depends(require_user_id)],
                   responses={code: {'model': ErrorResponse} for code in (401, 422, 500)})


def _clip_bounds(value, size):
    """단일 bytes=start-end/start-/-suffix만 받는다. 잘못된 범위는 None."""
    match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', value.strip(), re.IGNORECASE)
    if match is None or not any(match.groups()):
        return None
    first, last = match.groups()
    if not first:
        suffix = int(last)
        return (max(0, size - suffix), size - 1) if suffix > 0 else None
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    return (start, end) if start < size and start <= end else None


def _clip_chunks(handle, remaining):
    while remaining:
        chunk = handle.read(min(64 * 1024, remaining))
        if not chunk:
            raise OSError('Clip changed during transfer')
        remaining -= len(chunk)
        yield chunk


class _ClipResponse(StreamingResponse):
    """전송 완료/중단/실패 모두에서 미리 연 파일을 닫는다."""
    def __init__(self, handle, length, **kwargs):
        self.handle = handle
        super().__init__(_clip_chunks(handle, length), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.handle.close()


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


@router.get('/{event_id}/trajectory', response_class=Response, responses={
    200: {'content': {'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}},
    404: {'model': ErrorResponse}})
def get_mobile_event_trajectory(event_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
                                service: Annotated[MobileEventService, Depends(get_mobile_event_service)]):
    return Response(content=service.trajectory(event_id), media_type='image/jpeg',
                    headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/{event_id}/clip', response_class=StreamingResponse, responses={
    200: {'content': {'video/mp4': {'schema': {'type': 'string', 'format': 'binary'}}}},
    206: {'content': {'video/mp4': {'schema': {'type': 'string', 'format': 'binary'}}}},
    404: {'model': ErrorResponse}, 416: {'model': ErrorResponse}})
def get_mobile_event_clip(event_id: Annotated[int, Path(ge=1, le=2**63 - 1)], request: Request,
                           service: Annotated[MobileEventService, Depends(get_mobile_event_service)]):
    handle, size = service.open_clip(event_id)
    headers = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    try:
        ranges = request.headers.getlist('range')
        # ETag/Last-Modified 검증자를 제공하지 않으므로 If-Range는 전체 파일로 응답한다.
        value = ','.join(ranges) if ranges and 'if-range' not in request.headers else None
        bounds = (0, size - 1) if value is None else _clip_bounds(value, size)
        if bounds is None:
            handle.close()
            error = APIError(416, 'INVALID_CLIP_RANGE', '지원하는 단일 바이트 범위를 확인해 주세요.')
            return JSONResponse(status_code=416, content=error.body(),
                                headers=headers | {'Content-Range': f'bytes */{size}'})
        start, end = bounds
        length = end - start + 1
        handle.seek(start)
        headers['Content-Length'] = str(length)
        if value is not None:
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        return _ClipResponse(handle, length, status_code=206 if value is not None else 200,
                             media_type='video/mp4', headers=headers)
    except OSError:
        handle.close()
        raise APIError(404, 'EVENT_CLIP_NOT_FOUND', '이벤트 영상을 찾을 수 없습니다.') from None
    except BaseException:
        handle.close()
        raise


@router.get('/{event_id}', response_model=MobileEventDetail,
            responses={404: {'model': ErrorResponse}, 409: {'model': ErrorResponse}})
def get_mobile_event_detail(event_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
                            service: Annotated[MobileEventService, Depends(get_mobile_event_service)],
                            response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return service.detail(event_id)
