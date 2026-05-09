import cv2
from ultralytics import YOLO

RTSP_URL = "rtsp://192.168.10.2:8554/stream"

# YOLO26s 모델 로드
model = YOLO("yolo26s.pt")

# 감지하고 싶은 객체만 선택
TARGET_CLASSES = ["person", "apple", "cell phone"]

cap = cv2.VideoCapture(RTSP_URL)

if not cap.isOpened():
    print("RTSP 연결 실패")
    exit()

print("RTSP 연결 성공")

while True:
    ret, frame = cap.read()

    if not ret:
        print("프레임 수신 실패")
        continue

    # YOLO26s 추론
    results = model(frame, verbose=False)

    # 첫 번째 결과
    result = results[0]

    # 박스 정보 순회
    for box in result.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        class_name = model.names[cls_id]

        # 원하는 객체만 필터링
        if class_name not in TARGET_CLASSES:
            continue

        # confidence 낮은 결과 제거
        if conf < 0.5:
            continue

        # 바운딩박스 좌표
        x1, y1, x2, y2 = map(int, box.xyxy[0])

        # 박스 그리기
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # 라벨 표시
        label = f"{class_name} {conf:.2f}"
        cv2.putText(
            frame,
            label,
            (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2
        )

    cv2.imshow("RTSP YOLO26s Client", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()