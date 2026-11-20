"""고정 PAR bundle을 사용한 단일 이미지 추론과 표시용 후처리."""
import hashlib
from pathlib import Path

from PIL import Image
import torch
from torchvision import transforms as T
from torchvision.models import resnet34


DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent / 'weights' / 'par_serving_exp3.pt'
WEIGHTS_SHA256 = 'e3ec03c516ab6429dada23b285def0509478332028180361bc9ee19404033c69'


def age_group(age, gender):
    """선택된 원래 나이 클래스를 변환한다. 클래스 확률을 합산하지 않는다."""
    if gender not in ('Male', 'Female'):
        raise ValueError('지원하지 않는 성별 클래스입니다.')
    if age == '<=15':
        return '소년' if gender == 'Male' else '소녀'
    if age in ('16-30', '31-45'):
        return '청년'
    if age in ('46-60', '61+'):
        return '장년'
    raise ValueError('지원하지 않는 나이 클래스입니다.')


def decode_predictions(logits, bundle):
    """단일 선택은 softmax/argmax, 다중 선택은 sigmoid와 bundle 표시 규칙."""
    attributes = {}
    for name, labels in bundle['attributes'].items():
        scores = logits[name].detach().to(device='cpu', dtype=torch.float32)
        if scores.shape != (1, len(labels)) or not torch.isfinite(scores).all():
            raise ValueError('CNN 출력 크기 또는 값이 올바르지 않습니다.')
        scores = scores[0]
        if name not in bundle['multi_label']:
            attributes[name] = labels[scores.softmax(dim=0).argmax().item()]
            continue
        probabilities = scores.sigmoid()
        rule = bundle['display_rules'][name]
        selected = set()
        if rule['threshold'] is not None:
            selected.update(torch.where(probabilities >= rule['threshold'])[0].tolist())
        if rule['always_top']:
            selected.add(probabilities.argmax().item())
        attributes[name] = [label for index, label in enumerate(labels) if index in selected]
    attributes['age'] = age_group(attributes['age'], attributes['gender'])
    return {'model_version': bundle['model_version'], 'attributes': attributes}


class _PARModel(torch.nn.Module):
    def __init__(self, attributes):
        super().__init__()
        self.backbone = resnet34(weights=None)
        self.backbone.fc = torch.nn.Identity()
        self.heads = torch.nn.ModuleDict({
            name: torch.nn.Linear(512, len(labels)) for name, labels in attributes.items()
        })

    def forward(self, images):
        features = self.backbone(images)
        return {name: head(features) for name, head in self.heads.items()}


class AppearanceAnalyzer:
    """한 Worker에서 재사용할 모델. GUI/DB/HTTP에 의존하지 않는다."""
    def __init__(self, weights_path=DEFAULT_WEIGHTS_PATH, device='auto'):
        if device not in ('auto', 'cpu', 'cuda', 'cuda:0'):
            raise ValueError('실행 장치는 auto/cpu/cuda/cuda:0 중 선택하세요.')
        if device == 'auto':
            device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
        if device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError('CUDA 실행 장치를 사용할 수 없습니다.')
        self.device = torch.device(device)
        path = Path(weights_path)
        with path.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if digest != WEIGHTS_SHA256:
            raise ValueError('CNN 가중치가 확인한 exp3 원본과 다릅니다.')
        self.bundle = torch.load(path, map_location='cpu', weights_only=True)
        if (self.bundle['format'] != 'par-serving-bundle'
                or self.bundle['format_version'] != 1 or self.bundle['backbone'] != 'resnet34'):
            raise ValueError('지원하지 않는 CNN bundle입니다.')
        self._model = _PARModel(self.bundle['attributes'])
        self._model.load_state_dict(self.bundle['state_dict'], strict=True)
        self._model.eval().to(device=self.device, dtype=torch.float32)
        # 추론에는 가중치 사본이 든 bundle 전체를 계속 보관할 필요가 없다.
        self.bundle = {key: value for key, value in self.bundle.items() if key != 'state_dict'}
        self._preprocess = T.Compose([
            T.Resize(tuple(self.bundle['input_size']), interpolation=T.InterpolationMode.BILINEAR,
                     antialias=True),
            T.ToTensor(),
            T.Normalize(self.bundle['normalize']['mean'], self.bundle['normalize']['std']),
        ])

    def analyze(self, image_path):
        """완료된 대표 crop 한 장을 읽어 JSON 직렬화 가능한 dict를 반환한다."""
        with Image.open(image_path) as image:
            batch = self._preprocess(image.convert('RGB')).unsqueeze(0)
        with torch.inference_mode():
            logits = self._model(batch.to(self.device))
        return decode_predictions(logits, self.bundle)
