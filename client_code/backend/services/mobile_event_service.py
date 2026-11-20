"""모바일 목록·상세·이미지 조회와 검증된 클립 파일 열기를 제공한다."""
from datetime import datetime, time, timedelta, timezone
import os
import stat

from ..repositories.mobile_event_repository import MobileEventRepository
from ..errors import APIError
from ..schemas.cnn_result import CNNResult
from ..schemas.mobile_event import (KST, MobileEventCard, MobileEventCursor, MobileEventDetail,
                                    MobileEventListResponse)


class MobileEventService:
    def __init__(self, session_factory, storage):
        self.session_factory = session_factory
        self.storage = storage
        self.repository = MobileEventRepository()

    def detail(self, event_id):
        with self.session_factory() as db:
            row = self.repository.get_detail(db, event_id)
            if row is None:
                raise APIError(404, 'EVENT_NOT_FOUND', '등록되지 않은 이벤트입니다.', 'event_id')
            event, camera_name, source = row
            if event.event_type not in ('person_appeared', 'person_disappeared'):
                raise APIError(409, 'EVENT_DETAIL_NOT_SUPPORTED', '인물 이벤트만 상세 조회할 수 있습니다.', 'event_id')
            # 사라짐의 시각/타입은 유지하고 표시용 데이터만 연결된 등장으로부터 읽는다.
            urls = {kind + '_url': (f'/api/v1/mobile/events/{event.event_id}/{kind}'
                    if source is not None and getattr(source, kind + '_path') else None)
                    for kind in ('crop', 'trajectory', 'clip')}
            return MobileEventDetail(event_id=event.event_id, event_type=event.event_type,
                occurred_at=event.occurred_at, global_person_id=event.global_person_id,
                camera_id=event.camera_id, camera_name=camera_name,
                cnn_result=source.cnn_result if source is not None else None, **urls)

    def crop(self, event_id):
        return self._image(event_id, 'crop')

    def trajectory(self, event_id):
        return self._image(event_id, 'trajectory')

    def open_clip(self, event_id):
        with self.storage.lock, self.session_factory() as db:
            event = self.repository.get(db, event_id)
            if event is None:
                raise APIError(404, 'EVENT_NOT_FOUND', '등록되지 않은 이벤트입니다.', 'event_id')
            source = (self.repository.get(db, event.related_event_id)
                      if event.event_type == 'person_disappeared' else event)
            if source is None or source.event_type != 'person_appeared' or source.clip_path is None:
                raise APIError(404, 'EVENT_CLIP_NOT_FOUND', '이벤트 영상을 찾을 수 없습니다.')
            handle = None
            try:
                self.storage.validate_media(dict(crop_path=None, trajectory_path=None,
                    clip_path=source.clip_path, camera_id=source.camera_id, global_person_id=source.global_person_id))
                handle = (self.storage.root / source.clip_path).open('rb')
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                    raise ValueError('Completed clip required')
                return handle, info.st_size
            except (APIError, OSError, ValueError, RuntimeError):
                if handle is not None:
                    handle.close()
                raise APIError(404, 'EVENT_CLIP_NOT_FOUND', '이벤트 영상을 찾을 수 없습니다.') from None

    def _image(self, event_id, kind):
        error_code = f'EVENT_{kind.upper()}_NOT_FOUND'
        with self.storage.lock, self.session_factory() as db:
            event = self.repository.get(db, event_id)
            if event is None:
                raise APIError(404, 'EVENT_NOT_FOUND', '등록되지 않은 이벤트입니다.', 'event_id')
            source = (self.repository.get(db, event.related_event_id)
                      if event.event_type == 'person_disappeared' else event)
            path = getattr(source, kind + '_path') if source is not None else None
            if source is None or source.event_type != 'person_appeared' or path is None:
                raise APIError(404, error_code, '이벤트 이미지를 찾을 수 없습니다.')
            try:
                # 요청한 이미지 하나만 검증한다. 다른 미디어의 완료/소실 여부와 분리한다.
                media = dict(crop_path=None, clip_path=None, trajectory_path=None)
                media[kind + '_path'] = path
                self.storage.validate_media(media | dict(camera_id=source.camera_id,
                                                        global_person_id=source.global_person_id))
                content = (self.storage.root / path).read_bytes()
                if not content:
                    raise ValueError('Empty image')
            except (APIError, OSError, ValueError, RuntimeError):
                # 저장소/파일 소실 및 경로 검증 실패에 내부 경로를 노출하지 않는다.
                raise APIError(404, error_code, '이벤트 이미지를 찾을 수 없습니다.') from None
            return content

    def list(self, query):
        start = datetime.combine(query.date, time.min, KST).astimezone(timezone.utc)
        with self.session_factory() as db:
            rows = self.repository.list(db, query, start, start + timedelta(days=1))
            items = []
            for event, camera_name, crop_path, cnn_result in rows[:query.limit]:
                person = event.event_type in ('person_appeared', 'person_disappeared')
                attributes = CNNResult.model_validate(cnn_result).attributes if person and cnn_result is not None else None
                items.append(MobileEventCard(
                    event_id=event.event_id, event_type=event.event_type,
                    global_person_id=event.global_person_id, camera_id=event.camera_id,
                    camera_name=camera_name, occurred_at=event.occurred_at,
                    crop_url=f'/api/v1/mobile/events/{event.event_id}/crop' if person and crop_path else None,
                    estimated_gender=attributes.gender if attributes else None,
                    estimated_age=attributes.age if attributes else None))
            cursor = None
            if len(rows) > query.limit:
                last = items[-1]
                cursor = MobileEventCursor(before_occurred_at=last.occurred_at, before_event_id=last.event_id)
            return MobileEventListResponse(date=query.date, items=items, next_cursor=cursor)
