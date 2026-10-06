"""카메라 DTO. PATCH 최종 조합 검증은 기존 값과 병합 후 CameraCreate로 수행한다."""

from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator, AnyUrl, AwareDatetime, BaseModel, ConfigDict, Field,
    StrictBool, StringConstraints, TypeAdapter, UrlConstraints,
    field_serializer, model_validator,
)


CameraType = Literal["webcam", "edge_node"]
CameraName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
WebcamIndex = Annotated[int, Field(strict=True, ge=0)]
_url_adapter = TypeAdapter(Annotated[AnyUrl, UrlConstraints(host_required=True)])


def _validate_url(value: str, *, edge_api: bool = False) -> str:
    # 파서는 검증에만 사용한다. 정규화된 URL로 원본 주소를 덮어쓰지 않는다.
    value = value.strip()
    try:
        if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError
        if "#" in value or "\\" in value or (edge_api and "?" in value):
            raise ValueError
        parsed = urlsplit(value)
        schemes = {"http", "https"} if edge_api else {"rtsp", "rtsps"}
        if parsed.scheme not in schemes or not parsed.hostname or "@" in parsed.netloc:
            raise ValueError
        if parsed.netloc.endswith(":") or (parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError
        _url_adapter.validate_python(value)
    except ValueError:
        raise ValueError("유효한 자격증명 없는 장치 주소가 필요합니다.") from None
    return value


def _validate_edge_url(value: str) -> str:
    return _validate_url(value, edge_api=True)


RtspUrl = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_url)]
EdgeApiUrl = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_edge_url)]


class CameraCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: CameraName
    camera_type: CameraType
    webcam_index: WebcamIndex | None = None
    rtsp_url: RtspUrl | None = Field(default=None, repr=False)
    edge_api_base_url: EdgeApiUrl | None = Field(default=None, repr=False)
    is_enabled: StrictBool = True

    @model_validator(mode="after")
    def validate_source(self):
        if self.camera_type == "webcam":
            if self.webcam_index is None or self.rtsp_url is not None or self.edge_api_base_url is not None:
                raise ValueError("웹캠은 장치 번호가 필요하며 장치 주소를 지정할 수 없습니다.")
        elif self.rtsp_url is None or self.webcam_index is not None:
            raise ValueError("엣지노드는 RTSP 주소가 필요하며 웹캠 번호를 지정할 수 없습니다.")
        return self


class CameraUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: CameraName | None = None
    webcam_index: WebcamIndex | None = None
    rtsp_url: RtspUrl | None = Field(default=None, repr=False)
    edge_api_base_url: EdgeApiUrl | None = Field(default=None, repr=False)
    is_enabled: StrictBool | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("수정할 필드를 하나 이상 지정해야 합니다.")
        for field in ("name", "is_enabled"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field}은 null일 수 없습니다.")
        return self


class CameraSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    camera_id: Annotated[int, Field(strict=True, ge=1)]
    name: CameraName
    camera_type: CameraType
    is_enabled: StrictBool
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_utc(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class CameraResponse(CameraSummary):
    webcam_index: WebcamIndex | None
    rtsp_url: RtspUrl | None = Field(repr=False)
    edge_api_base_url: EdgeApiUrl | None = Field(repr=False)


class CameraListResponse(BaseModel):
    items: list[CameraSummary]
