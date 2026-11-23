"""2단계 기준 모델. PC가 달라도 같은 가중치와 전처리를 사용한다."""
from pathlib import Path

MODEL_NAME = 'osnet_x1_0'
UPSTREAM_REVISION = 'f8cd150fdf77e8d9e1ed143b7f308c2c609ded50'
WEIGHTS_REVISION = 'a5c5cc037c24235cda3b21085b93ad77c9616224'
WEIGHTS_FILENAME = (
    'osnet_x1_0_msmt17_combineall_256x128_amsgrad_ep150_stp60_'
    'lr0.0015_b64_fb10_softmax_labelsmooth_flip_jitter.pth'
)
WEIGHTS_URL = f'https://huggingface.co/kaiyangzhou/osnet/resolve/{WEIGHTS_REVISION}/{WEIGHTS_FILENAME}'
WEIGHTS_SHA256 = '48df972f72887b95cf3b43b3a07c3a7d2398381aea0f9cae64a7ef11d512b727'
WEIGHTS_SIZE = 17273805
DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent / 'weights' / 'osnet_x1_0_msmt17_combineall.pth'
IMAGE_SIZE = (256, 128)  # H, W
PIXEL_MEAN = (0.485, 0.456, 0.406)
PIXEL_STD = (0.229, 0.224, 0.225)
CROP_COUNT = 5
DEFAULT_DEVICE = 'auto'
SIMILARITY_THRESHOLD = 0.60
