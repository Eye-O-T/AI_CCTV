import torch
import torch.nn.functional as F

from huggingface_hub import hf_hub_download
from torchreid.utils import FeatureExtractor


# -------------------------------------------------
# 1. 비교할 사진
# -------------------------------------------------

IMAGE1 = "person1.png"
IMAGE2 = "person2.png"


# -------------------------------------------------
# 2. OSNet Re-ID 가중치 다운로드
# -------------------------------------------------

MODEL_FILENAME = (
    "osnet_ain_x1_0_msmt17_256x128_amsgrad_ep50_"
    "lr0.0015_coslr_b64_fb10_softmax_labsmth_flip_jitter.pth"
)

model_path = hf_hub_download(
    repo_id="kaiyangzhou/osnet",
    filename=MODEL_FILENAME
)


# -------------------------------------------------
# 3. OSNet 모델 준비
# -------------------------------------------------

device = "cuda" if torch.cuda.is_available() else "cpu"

print("사용 장치:", device)

extractor = FeatureExtractor(
    model_name="osnet_ain_x1_0",
    model_path=model_path,
    device=device
)


# -------------------------------------------------
# 4. 두 사진에서 특징 벡터 추출
# -------------------------------------------------

features = extractor([
    IMAGE1,
    IMAGE2
])


# -------------------------------------------------
# 5. 특징 벡터 정규화
# -------------------------------------------------

features = F.normalize(
    features,
    p=2,
    dim=1
)

feature1 = features[0]
feature2 = features[1]


# -------------------------------------------------
# 6. Cosine Similarity 계산
# -------------------------------------------------

similarity = F.cosine_similarity(
    feature1.unsqueeze(0),
    feature2.unsqueeze(0)
).item()


# -------------------------------------------------
# 7. 임시 threshold
# -------------------------------------------------

THRESHOLD = 0.65

print()
print("========== Re-ID 결과 ==========")
print(f"유사도: {similarity:.4f}")
print(f"기준값: {THRESHOLD:.2f}")

if similarity >= THRESHOLD:
    print("판정: 같은 사람")
else:
    print("판정: 다른 사람")