"""모바일 카드 목록 계약. 기존 이벤트 DTO/조회 계약은 변경하지 않는다."""
from datetime import date as CalendarDate, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_serializer, field_validator

from .event import EventListParameters, EventTime, EventType, PositiveId, _query_integer
from .cnn_result import CNNResult


KST = timezone(timedelta(hours=9))


class MobileEventListParameters(EventListParameters):
    date: CalendarDate
    limit: Annotated[int, BeforeValidator(_query_integer), Field(strict=True, ge=1, le=100)] = 30

    @field_validator('date', mode='before')
    @classmethod
    def calendar_date(cls, value):
        if not isinstance(value, str):
            raise ValueError('YYYY-MM-DD 날짜가 필요합니다.')
        parsed = CalendarDate.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError('YYYY-MM-DD 날짜가 필요합니다.')
        # UTC 변환과 다음 날짜 계산이 가능한 범위만 허용한다.
        try:
            start = datetime.combine(parsed, time.min, KST).astimezone(timezone.utc)
            start + timedelta(days=1)
        except (OverflowError, ValueError):
            raise ValueError('조회 가능한 날짜 범위를 확인하세요.') from None
        return parsed


class MobileEventCard(BaseModel):
    event_id: PositiveId
    event_type: EventType
    global_person_id: PositiveId | None
    camera_id: PositiveId
    camera_name: str
    occurred_at: EventTime
    crop_url: str | None
    estimated_gender: Literal['Male', 'Female'] | None
    estimated_age: Literal['소년', '소녀', '청년', '장년'] | None

    @field_serializer('occurred_at', when_used='json')
    def serialize_utc(self, value):
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class MobileEventCursor(BaseModel):
    before_occurred_at: EventTime
    before_event_id: PositiveId

    @field_serializer('before_occurred_at', when_used='json')
    def serialize_utc(self, value):
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class MobileEventListResponse(BaseModel):
    date: CalendarDate
    items: list[MobileEventCard]
    next_cursor: MobileEventCursor | None


class MobileEventDetail(BaseModel):
    event_id: PositiveId
    event_type: Literal['person_appeared', 'person_disappeared']
    occurred_at: EventTime
    global_person_id: PositiveId
    camera_id: PositiveId
    camera_name: str
    cnn_result: CNNResult | None
    crop_url: str | None
    trajectory_url: str | None
    clip_url: str | None

    @field_serializer('occurred_at', when_used='json')
    def serialize_utc(self, value):
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
