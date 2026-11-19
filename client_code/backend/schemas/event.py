"""이벤트 입력 조합·UTC·상대 경로 계약. 실재/저장소 검증은 Service 책임이다."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field,
                      UUID4, field_serializer, field_validator, model_validator)


def _datetime_input(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError('시간대가 포함된 ISO 8601 시각이 필요합니다.')
    return value


def _relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('유효한 상대 경로가 필요합니다.')
    # Windows 경로/ADS/장치 경로와 모호한 정규화를 받지 않는다. 구분자는 /로 고정한다.
    if any(ord(c) < 32 or c in '\\:*?"<>|' for c in value):
        raise ValueError('상대 경로 구분자는 /를 사용합니다.')
    parts = value.split('/')
    if any(p in ('', '.', '..') or p != p.strip() or p.endswith('.') for p in parts):
        raise ValueError('경로에 빈 항목·상위 이동을 사용할 수 없습니다.')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
        prefix + number for prefix in ('COM', 'LPT') for number in '123456789¹²³'}
    if any(p.split('.')[0].upper() in reserved for p in parts):
        raise ValueError('Windows 장치 이름은 경로에 사용할 수 없습니다.')
    return value


PositiveId = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
Generation = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
EventTime = Annotated[AwareDatetime, BeforeValidator(_datetime_input)]
MediaPath = Annotated[str, BeforeValidator(_relative_path)]
EventType = Literal['person_appeared', 'person_disappeared', 'camera_started', 'camera_stopped']
MEDIA_FIELDS = ('crop_path', 'clip_path', 'trajectory_path')


class EventCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)

    event_type: EventType
    camera_id: PositiveId
    camera_execution_id: UUID4
    tracking_generation: Generation | None = None
    local_person_id: PositiveId | None = None
    global_person_id: PositiveId | None = None
    related_event_id: PositiveId | None = None
    occurred_at: EventTime
    crop_path: MediaPath | None = None
    clip_path: MediaPath | None = None
    trajectory_path: MediaPath | None = None

    @field_validator('camera_execution_id', mode='before')
    @classmethod
    def execution_uuid(cls, value):
        if not isinstance(value, str):
            raise ValueError('UUID v4 문자열이 필요합니다.')
        # UUID4만 사용하면 다른 버전도 강제 변환하지 않고 거부한다.
        return value

    @field_validator('occurred_at')
    @classmethod
    def normalize_utc(cls, value):
        try:
            return value.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            raise ValueError('UTC로 표현할 수 있는 시각이 필요합니다.') from None

    @model_validator(mode='after')
    def event_fields(self):
        person = self.event_type.startswith('person_')
        values = (self.tracking_generation, self.local_person_id, self.global_person_id)
        if (person and any(v is None for v in values)) or (not person and any(v is not None for v in values)):
            raise ValueError('이벤트 종류에 맞는 인물 식별값이 필요합니다.')
        if (self.event_type == 'person_disappeared') != (self.related_event_id is not None):
            raise ValueError('사라짐 이벤트만 등장 이벤트를 참조해야 합니다.')
        if self.event_type != 'person_appeared' and any(getattr(self, f) is not None for f in MEDIA_FIELDS):
            raise ValueError('미디어는 등장 이벤트에만 저장합니다.')
        return self

    @field_serializer('occurred_at', when_used='json')
    def serialize_utc(self, value):
        return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')


class EventMediaUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)

    crop_path: MediaPath | None = None
    clip_path: MediaPath | None = None
    trajectory_path: MediaPath | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError('갱신할 미디어 필드가 필요합니다.')
        return self


class EventResponse(EventCreate):
    model_config = ConfigDict(extra='forbid', from_attributes=True, hide_input_in_errors=True)

    event_id: PositiveId
    server_run_id: PositiveId


class EventListQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)

    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 50
    before_occurred_at: EventTime | None = None
    before_event_id: PositiveId | None = None

    @field_validator('before_occurred_at')
    @classmethod
    def normalize_cursor(cls, value):
        try:
            return value.astimezone(timezone.utc) if value is not None else None
        except (OverflowError, ValueError):
            raise ValueError('UTC로 표현할 수 있는 시각이 필요합니다.') from None

    @model_validator(mode='after')
    def paired_cursor(self):
        if (self.before_occurred_at is None) != (self.before_event_id is None):
            raise ValueError('커서 시각과 ID를 함께 전달해야 합니다.')
        return self


class EventListResponse(BaseModel):
    items: list[EventResponse]


def _query_integer(value):
    if isinstance(value, str):
        if not value or len(value) > 19 or any(c not in '0123456789' for c in value):
            raise ValueError('양의 정수 쿼리가 필요합니다.')
        return int(value)
    return value


class EventListParameters(EventListQuery):
    """HTTP 문자열만 정수로 변환하고 동일한 목록 계약을 적용한다."""
    limit: Annotated[int, BeforeValidator(_query_integer), Field(strict=True, ge=1, le=100)] = 50
    before_event_id: Annotated[int, BeforeValidator(_query_integer), Field(strict=True, ge=1, le=2**63 - 1)] | None = None
