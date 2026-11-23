"""추론 라이브러리 없이 공유하는 대표 벡터 형식과 검증."""
import math
import struct

FEATURE_DIMENSION = 512
FEATURE_BYTES = FEATURE_DIMENSION * 4
NORM_TOLERANCE = 0.001
_FORMAT = '<' + 'f' * FEATURE_DIMENSION


def encode_vector(values: list) -> bytes:
    if not isinstance(values, list) or len(values) != FEATURE_DIMENSION:
        raise ValueError('512차원 숫자 배열이 필요합니다.')
    if any(type(value) not in (int, float) for value in values):
        raise ValueError('벡터에는 숫자만 사용할 수 있습니다.')
    try:
        blob = struct.pack(_FORMAT, *values)
    except (OverflowError, struct.error):
        raise ValueError('float32 벡터가 필요합니다.') from None
    _validate_values(struct.unpack(_FORMAT, blob))
    return blob


def _validate_values(values):
    if not all(math.isfinite(value) for value in values):
        raise ValueError('유한한 벡터가 필요합니다.')
    norm = math.sqrt(math.fsum(value * value for value in values))
    if norm == 0 or abs(norm - 1) > NORM_TOLERANCE:
        raise ValueError('L2 정규화된 벡터가 필요합니다.')


def decode_vector(blob: bytes) -> list[float]:
    if not isinstance(blob, bytes) or len(blob) != FEATURE_BYTES:
        raise ValueError('벡터 저장 형식이 올바르지 않습니다.')
    values = struct.unpack(_FORMAT, blob)
    _validate_values(values)
    return list(values)
