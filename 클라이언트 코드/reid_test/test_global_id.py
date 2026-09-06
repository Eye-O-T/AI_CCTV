import cv2
import torch
import torch.nn.functional as F

from ultralytics import YOLO
from huggingface_hub import hf_hub_download
from torchreid.utils import FeatureExtractor


# =========================================================
# 설정
# =========================================================

VIDEO1 = "video01.mp4"
VIDEO2 = "video02.mp4"

# 몇 프레임마다 사람 특징을 추출할지
SAMPLE_INTERVAL = 15

# 영상 하나에서 사용할 최대 Crop 개수
MAX_SAMPLES = 10

# 동일인 판별 임시 기준값
THRESHOLD = 0.65


# =========================================================
# OSNet 모델 다운로드 / 로드
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
# YOLO 로드
# =========================================================

yolo = YOLO("yolo26s.pt")

person_class_id = None

for class_id, class_name in yolo.names.items():
    if class_name == "person":
        person_class_id = class_id
        break

if person_class_id is None:
    raise RuntimeError("YOLO 모델에서 person 클래스를 찾지 못했습니다.")


# =========================================================
# 영상 하나에서 대표 특징 벡터 추출
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

        # 10프레임마다 처리
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

        # 영상에 한 사람만 있다고 가정
        # 여러 박스가 나오면 가장 큰 사람 사용
        largest_box = None
        largest_area = 0

        for box in boxes:

            x1, y1, x2, y2 = map(
                int,
                box.xyxy[0].tolist()
            )

            width = x2 - x1
            height = y2 - y1

            area = width * height

            if area > largest_area:
                largest_area = area
                largest_box = (x1, y1, x2, y2)

        if largest_box is None:
            continue

        x1, y1, x2, y2 = largest_box

        h, w = frame.shape[:2]

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        if x2 <= x1 or y2 <= y1:
            continue

        # 사람 부분 Crop
        person_crop = frame[y1:y2, x1:x2]

        if person_crop.size == 0:
            continue

        # OpenCV BGR -> RGB
        person_crop = cv2.cvtColor(
            person_crop,
            cv2.COLOR_BGR2RGB
        )

        person_crops.append(person_crop)

        # 최대 10개만 사용
        if len(person_crops) >= MAX_SAMPLES:
            break

    cap.release()

    if len(person_crops) == 0:
        raise RuntimeError(
            f"{video_path}에서 사람을 찾지 못했습니다."
        )

    print(
        f"{video_path}: "
        f"{len(person_crops)}개 Crop 사용"
    )

    # OSNet 특징벡터 추출
    features = extractor(person_crops)

    # 각 특징벡터 정규화
    features = F.normalize(
        features,
        p=2,
        dim=1
    )

    # 여러 프레임 특징벡터 평균
    mean_feature = features.mean(
        dim=0,
        keepdim=True
    )

    # 평균 특징벡터 다시 정규화
    mean_feature = F.normalize(
        mean_feature,
        p=2,
        dim=1
    )

    return mean_feature


# =========================================================
# Global ID 관리자
# =========================================================

class GlobalIDManager:

    def __init__(self, threshold=0.65):

        self.threshold = threshold

        # Global ID -> 특징벡터
        self.global_features = {}

        # (camera_id, local_id) -> global_id
        self.local_to_global = {}

        self.next_global_id = 1


    def assign(self, camera_id, local_id, feature):

        key = (camera_id, local_id)

        # 이미 등록된 Local ID
        if key in self.local_to_global:
            global_id = self.local_to_global[key]

            return global_id, 1.0


        best_global_id = None
        best_similarity = -1.0


        # 기존 Global ID들과 비교
        for global_id, saved_feature in self.global_features.items():

            similarity = F.cosine_similarity(
                feature,
                saved_feature
            ).item()

            if similarity > best_similarity:

                best_similarity = similarity
                best_global_id = global_id


        # 기존 사람과 동일인으로 판단
        if (
            best_global_id is not None
            and best_similarity >= self.threshold
        ):

            self.local_to_global[key] = best_global_id

            return best_global_id, best_similarity


        # 새로운 사람
        new_global_id = self.next_global_id

        self.next_global_id += 1

        self.global_features[new_global_id] = feature

        self.local_to_global[key] = new_global_id

        return new_global_id, best_similarity


# =========================================================
# 실행
# =========================================================

print()
print("CAM1 영상 분석 중...")

feature1 = extract_video_feature(VIDEO1)


print()
print("CAM2 영상 분석 중...")

feature2 = extract_video_feature(VIDEO2)


# =========================================================
# Global ID 부여
# =========================================================

manager = GlobalIDManager(
    threshold=THRESHOLD
)


# 현재 테스트에서는 영상마다 사람이 한 명이라
# Local ID를 임시로 1로 설정
global_id1, similarity1 = manager.assign(
    camera_id="CAM1",
    local_id=1,
    feature=feature1
)


global_id2, similarity2 = manager.assign(
    camera_id="CAM2",
    local_id=1,
    feature=feature2
)


# =========================================================
# 결과
# =========================================================

print()
print("====================================")
print("          Global ID 결과")
print("====================================")

print(
    f"CAM1 Local ID 1 -> Global ID {global_id1}"
)

print(
    f"CAM2 Local ID 1 -> Global ID {global_id2}"
)

print()

print(
    f"두 영상 인물 유사도: {similarity2:.4f}"
)

print(
    f"동일인 기준값: {THRESHOLD:.2f}"
)

print()

if global_id1 == global_id2:

    print("판정: 같은 사람")
    print(
        f"두 카메라 모두 Global ID {global_id1}"
    )

else:

    print("판정: 다른 사람")
    print(
        f"CAM1 = Global ID {global_id1}"
    )
    print(
        f"CAM2 = Global ID {global_id2}"
    )