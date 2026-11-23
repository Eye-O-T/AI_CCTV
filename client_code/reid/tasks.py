"""카메라와 독립적인 관측·매칭 입력·결과. 벡터는 repr에서 제외한다."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

if __package__ == 'reid':
    from feature_vector import decode_vector, encode_vector
else:
    from ..feature_vector import decode_vector, encode_vector


@dataclass(frozen=True)
class Observation:
    task_id: UUID
    camera_id: int
    execution_id: str
    tracking_generation: int
    local_person_id: int
    observed_at: datetime

    def __post_init__(self):
        task_id = UUID(str(self.task_id))
        if task_id.version != 4 or UUID(self.execution_id).version != 4:
            raise ValueError('관측과 실행에는 UUID v4가 필요합니다.')
        if any(type(value) is not int or value < 1 for value in (self.camera_id, self.local_person_id)):
            raise ValueError('카메라·로컬 인물 ID는 양의 정수여야 합니다.')
        if type(self.tracking_generation) is not int or self.tracking_generation < 0:
            raise ValueError('추적 세대는 0 이상 정수여야 합니다.')
        if not isinstance(self.observed_at, datetime) or self.observed_at.utcoffset() is None:
            raise ValueError('시간대가 포함된 관측 시각이 필요합니다.')
        object.__setattr__(self, 'task_id', task_id)
        object.__setattr__(self, 'execution_id', UUID(self.execution_id).hex)
        object.__setattr__(self, 'observed_at', self.observed_at.astimezone(timezone.utc))

    @property
    def local_key(self):
        return (self.camera_id, self.execution_id, self.tracking_generation, self.local_person_id)


@dataclass(frozen=True)
class MatchTask:
    observation: Observation
    feature_vector: tuple[float, ...] = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.observation, Observation):
            raise ValueError('관측 정보가 필요합니다.')
        values = decode_vector(encode_vector(list(self.feature_vector)))
        object.__setattr__(self, 'feature_vector', tuple(values))


@dataclass(frozen=True)
class MatchResult:
    observation: Observation
    global_person_id: int
    mode: str
    similarity: float | None
