"""관리자용 저장소 설정 DTO. 설정에는 최상위 AI_CCTV 폴더를 전달한다."""
from pydantic import BaseModel, ConfigDict, Field


class StorageUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)

    storage_root: str = Field(strict=True, min_length=1, max_length=1024)


class StorageResponse(BaseModel):
    storage_root: str | None
    locked: bool
