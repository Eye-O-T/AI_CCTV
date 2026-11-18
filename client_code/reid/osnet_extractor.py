"""저장된 crop 5장을 512차원 대표 벡터로 변환한다. DB/카메라는 접근하지 않는다."""
from collections.abc import Mapping
from pathlib import Path
from threading import Lock

from PIL import Image
import torch
from torchvision import transforms as T

# 현재 PyQt의 script 실행과 저장소 루트의 package 실행을 모두 지원한다.
if __package__ == 'reid':
    from feature_vector import FEATURE_DIMENSION, decode_vector, encode_vector
else:
    from ..feature_vector import FEATURE_DIMENSION, decode_vector, encode_vector
from .config import (
    CROP_COUNT, DEFAULT_DEVICE, DEFAULT_WEIGHTS_PATH, IMAGE_SIZE, PIXEL_MEAN, PIXEL_STD,
)
from .download_weights import weights_match
from .vendor.osnet import osnet_x1_0

_MESSAGES = {
    'WEIGHTS_NOT_FOUND': 'OSNet 가중치 파일이 없습니다. 준비 명령을 먼저 실행하세요.',
    'WEIGHTS_HASH_MISMATCH': 'OSNet 가중치가 기준 파일과 다릅니다.',
    'MODEL_LOAD_FAILED': 'OSNet 모델을 로딩하지 못했습니다.',
    'DEVICE_UNAVAILABLE': '요청한 OSNet 실행 장치를 사용할 수 없습니다.',
    'INVALID_CROP_COUNT': '서로 다른 crop 이미지 경로 5개가 필요합니다.',
    'IMAGE_READ_FAILED': 'crop 이미지를 읽지 못했습니다.',
    'INFERENCE_FAILED': 'OSNet 특징 추출에 실패했습니다.',
    'INVALID_FEATURES': 'OSNet 특징 벡터가 올바르지 않습니다.',
}


class OSNetError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES[code])


def representative_vector(features):
    """이미지별 L2 → 산술평균 → L2. 잘못된 특징을 영벡터로 대체하지 않는다."""
    if not isinstance(features, torch.Tensor) or features.shape != (CROP_COUNT, FEATURE_DIMENSION):
        raise OSNetError('INVALID_FEATURES')
    features = features.detach().to(device='cpu', dtype=torch.float32)
    if not torch.isfinite(features).all():
        raise OSNetError('INVALID_FEATURES')
    norms = torch.linalg.vector_norm(features, dim=1, keepdim=True)
    if not torch.isfinite(norms).all() or (norms <= 1e-12).any():
        raise OSNetError('INVALID_FEATURES')
    averaged = (features / norms).mean(dim=0)
    norm = torch.linalg.vector_norm(averaged)
    if not torch.isfinite(norm) or norm <= 1e-12:
        raise OSNetError('INVALID_FEATURES')
    try:
        return decode_vector(encode_vector((averaged / norm).tolist()))
    except ValueError:
        raise OSNetError('INVALID_FEATURES') from None


class OSNetExtractor:
    """인스턴스 하나를 카메라별 Worker가 공유한다. forward는 잠금으로 직렬화한다."""

    def __init__(self, weights_path=DEFAULT_WEIGHTS_PATH, device=DEFAULT_DEVICE):
        self._lock = Lock()
        self.device = self._select_device(device)
        path = Path(weights_path)
        try:
            if not path.is_file():
                raise OSNetError('WEIGHTS_NOT_FOUND')
            if not weights_match(path):
                raise OSNetError('WEIGHTS_HASH_MISMATCH')
        except OSError:
            raise OSNetError('WEIGHTS_NOT_FOUND') from None
        try:
            checkpoint = torch.load(path, map_location='cpu', weights_only=True)
            state = checkpoint.get('state_dict', checkpoint) if isinstance(checkpoint, Mapping) else None
            if not isinstance(state, Mapping):
                raise ValueError
            state = {key.removeprefix('module.'): value for key, value in state.items()}
            model = osnet_x1_0()
            # 학습용 분류기만 제외하고 특징 추출 파라미터 전체를 확인한다.
            state = {key: value for key, value in state.items() if key not in ('classifier.weight', 'classifier.bias')}
            expected = set(model.state_dict()) - {'classifier.weight', 'classifier.bias'}
            if set(state) != expected:
                raise ValueError
            if any(not isinstance(value, torch.Tensor) or not torch.isfinite(value).all() for value in state.values()):
                raise ValueError
            loaded = model.load_state_dict(state, strict=False)
            if set(loaded.missing_keys) != {'classifier.weight', 'classifier.bias'} or loaded.unexpected_keys:
                raise ValueError
            self._model = model.eval().to(device=self.device, dtype=torch.float32)
        except Exception:
            raise OSNetError('MODEL_LOAD_FAILED') from None
        self._preprocess = T.Compose([
            T.Resize(IMAGE_SIZE, interpolation=T.InterpolationMode.BILINEAR, antialias=True),
            T.ToTensor(), T.Normalize(PIXEL_MEAN, PIXEL_STD),
        ])

    @staticmethod
    def _select_device(device):
        if device not in ('auto', 'cpu', 'cuda', 'cuda:0'):
            raise OSNetError('DEVICE_UNAVAILABLE')
        if device == 'auto':
            return torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        if device.startswith('cuda') and not torch.cuda.is_available():
            raise OSNetError('DEVICE_UNAVAILABLE')
        return torch.device(device)

    def extract(self, crop_paths):
        if not isinstance(crop_paths, (list, tuple)) or len(crop_paths) != CROP_COUNT:
            raise OSNetError('INVALID_CROP_COUNT')
        try:
            paths = [Path(path).resolve() for path in crop_paths]
            if len(set(paths)) != CROP_COUNT:
                raise OSNetError('INVALID_CROP_COUNT')
            images = []
            for path in paths:
                with Image.open(path) as image:
                    images.append(self._preprocess(image.convert('RGB')))
            batch = torch.stack(images)
        except OSNetError:
            raise
        except Exception:
            raise OSNetError('IMAGE_READ_FAILED') from None
        try:
            with self._lock, torch.inference_mode():
                features = self._model(batch.to(self.device)).cpu()
                return representative_vector(features)
        except OSNetError:
            raise
        except Exception:
            raise OSNetError('INFERENCE_FAILED') from None
