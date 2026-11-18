"""글로벌 인물 요청/응답 DTO. 원본 벡터는 repr에 포함하지 않는다."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, UUID4,
    field_serializer, field_validator,
)

from ...feature_vector import decode_vector, encode_vector


def _vector(value):
    return decode_vector(encode_vector(value))


def _datetime_input(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise ValueError('시간대가 포함된 ISO 8601 시각이 필요합니다.') from None
    if not isinstance(value, (str, datetime)):
        raise ValueError('시간대가 포함된 ISO 8601 시각이 필요합니다.')
    return value


Vector = Annotated[list[float], BeforeValidator(_vector), Field(min_length=512, max_length=512)]


class LastSeenRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)

    observed_at: Annotated[AwareDatetime, BeforeValidator(_datetime_input)]

    @field_validator('observed_at')
    @classmethod
    def normalize_utc(cls, value):
        try:
            return value.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            raise ValueError('UTC로 표현할 수 있는 시각이 필요합니다.') from None


class GlobalPersonCreate(LastSeenRequest):
    request_id: UUID4
    feature_vector: Vector = Field(repr=False)

    @field_validator('request_id', mode='before')
    @classmethod
    def string_uuid(cls, value):
        if not isinstance(value, str):
            raise ValueError('UUID v4 문자열이 필요합니다.')
        return value


class GlobalPersonResponse(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    global_person_id: Annotated[int, Field(strict=True, ge=1)]
    feature_vector: Vector = Field(repr=False)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    last_seen_at: AwareDatetime

    @field_serializer('created_at', 'updated_at', 'last_seen_at', when_used='json')
    def serialize_utc(self, value):
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class GlobalPersonListResponse(BaseModel):
    feature_dimension: Literal[512] = 512
    items: list[GlobalPersonResponse]
