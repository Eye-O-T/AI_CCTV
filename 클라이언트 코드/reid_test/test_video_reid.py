import cv2
import torch
import torch.nn.functional as F

from ultralytics import YOLO
from huggingface_hub import hf_hub_download
from torchreid.utils import FeatureExtractor


# =========================================================
# 설정
# =========================================================

VIDEO1 = "video1.mp4"
VIDEO2 = "video2.mp4"

# 몇 프레임마다 Re-ID 특징을 뽑을지
SAMPLE_INTERVAL = 15

# 임시 기준값
THRESHOLD = 0.65


# =========================================================
# OSNet 모델 준비
# =========================================================

MODEL_FILENAME = (
    "osnet_ain_x1_0_msmt17_256x128_amsgrad_ep50_"
    "lr0.0015_coslr_b64_fb10_softmax_labsmth_flip_jitter.pth"
)

model_path = hf_hub_download(
    repo_id="kaiyangzhou/osnet",
    filename=MODEL_FILENAME
)

device = "cuda" if torch.cuda.is_available() else "cpu"

print("사용 장치:", device)

extractor = FeatureExtractor(
    model_name="osnet_ain_x1_0",
    model_path=model_path,
    device=device
)


# =========================================================
# YOLO 준비
# =========================================================

yolo = YOLO("yolo26s.pt")

# person class ID 찾기
person_class_id = None

for class_id, class_name in yolo.names.items():
    if class_name == "person":
        person_class_id = class_id
        break

if person_class_id is None:
    raise RuntimeError("YOLO 모델에서 person 클래스를 찾을 수 없습니다.")


# =========================================================
# 한 영상에서 대표 특징벡터 추출
# =========================================================

def extract_video_feature(video_path):

    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video_path}")

    frame_number = 0
    person_crops = []

    while True:

        ret, frame = cap.read()

        if not ret:
            break

        frame_number += 1

        # 모든 프레임을 처리하지 않고 일부만 사용
        if frame_number % SAMPLE_INTERVAL != 0:
            continue

        # YOLO 사람 탐지
        results = yolo.predict(
            frame,
            classes=[person_class_id],
            conf=0.4,
            verbose=False
        )

        if not results:
            continue

        boxes = results[0].boxes

        if boxes is None or len(boxes) == 0:
            continue

        # -------------------------------------------------
        # 현재는 영상에 사람이 1명이라고 가정.
        # 여러 사람이 잡히면 가장 큰 사람 bbox 사용.
        # -------------------------------------------------

        largest_box = None
        largest_area = 0

        for box in boxes:

            x1, y1, x2, y2 = map(int, box.xyxy[0])

            width = x2 - x1
            height = y2 - y1
            area = width * height

            if area > largest_area:
                largest_area = area
                largest_box = (x1, y1, x2, y2)

        if largest_box is None:
            continue

        x1, y1, x2, y2 = largest_box

        # 화면 범위 보호
        h, w = frame.shape[:2]

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        person_crop = frame[y1:y2, x1:x2]

        if person_crop.size == 0:
            continue

        # OpenCV는 BGR
        # OSNet에 넣기 전에 RGB로 변환
        person_crop = cv2.cvtColor(
            person_crop,
            cv2.COLOR_BGR2RGB
        )

        person_crops.append(person_crop)

    cap.release()

    if len(person_crops) == 0:
        raise RuntimeError(
            f"{video_path}에서 사람 Crop을 얻지 못했습니다."
        )

    print(
        f"{video_path}: "
        f"{len(person_crops)}개의 사람 Crop 추출"
    )

    # =====================================================
    # Crop들을 한 번에 OSNet에 입력
    # =====================================================

    features = extractor(person_crops)

    # 각각의 특징 벡터 정규화
    features = F.normalize(
        features,
        p=2,
        dim=1
    )

    # 모든 프레임 특징의 평균
    mean_feature = features.mean(
        dim=0,
        keepdim=True
    )

    # 평균 특징도 다시 정규화
    mean_feature = F.normalize(
        mean_feature,
        p=2,
        dim=1
    )

    return mean_feature


# =========================================================
# 영상 1
# =========================================================

print()
print("영상 1 분석 중...")

feature1 = extract_video_feature(VIDEO1)


# =========================================================
# 영상 2
# =========================================================

print()
print("영상 2 분석 중...")

feature2 = extract_video_feature(VIDEO2)


# =========================================================
# 두 영상 비교
# =========================================================

similarity = F.cosine_similarity(
    feature1,
    feature2
).item()


print()
print("========== 영상 Re-ID 결과 ==========")

print(f"유사도: {similarity:.4f}")
print(f"기준값: {THRESHOLD:.2f}")

if similarity >= THRESHOLD:
    print("판정: 같은 사람")
else:
    print("판정: 다른 사람")