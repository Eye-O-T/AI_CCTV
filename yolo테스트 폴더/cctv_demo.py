import cv2
import time
import numpy as np
import random
from collections import defaultdict
from ultralytics import YOLO

# 1. 모델 로드 및 설정
model = YOLO('yolo26n.pt') 
class_names = model.names # YOLO가 학습한 80종의 이름 리스트
track_history = defaultdict(lambda: []) 

# ID별 고유 색상을 저장할 딕셔너리
track_colors = {}

def get_color(track_id):
    """ID마다 고유한 색상을 반환 (없으면 생성)"""
    if track_id not in track_colors:
        # 시인성이 좋은 밝은 색상 위주로 랜덤 생성
        track_colors[track_id] = (random.randint(50, 255), random.randint(50, 255), random.randint(50, 255))
    return track_colors[track_id]

cap = cv2.VideoCapture(0)
is_recording = False
out = None
last_detected_time = 0
wait_time = 3 

while cap.isOpened():
    success, frame = cap.read()
    if not success: break

    # 2. 객체 추적
    results = model.track(frame, persist=True, verbose=False)
    annotated_frame = frame.copy() 

    if results[0].boxes.id is not None:
        boxes = results[0].boxes.xywh.cpu().numpy()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        class_ids = results[0].boxes.cls.int().cpu().tolist() # 클래스 번호 가져오기

        for box, track_id, class_id in zip(boxes, track_ids, class_ids):
            x, y, w, h = box
            
            # ID별 고유 색상 가져오기
            color = get_color(track_id)
            
            # 궤적 업데이트
            track = track_history[track_id]
            track.append((float(x), float(y)))
            if len(track) > 30:
                track.pop(0)

            # 궤적 선 그리기
            points = np.hstack(track).astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(annotated_frame, [points], isClosed=False, color=color, thickness=2)
            
            # 객체 이름 판단 (학습된 객체 vs 모르는 객체)
            obj_name = class_names[class_id] if class_id in class_names else "모르는 객체"
            
            # 바운딩 박스 및 정보 표시
            label = f"{obj_name} (ID: {track_id})"
            cv2.rectangle(annotated_frame, (int(x-w/2), int(y-h/2)), (int(x+w/2), int(y+h/2)), color, 2)
            cv2.putText(annotated_frame, label, (int(x-w/2), int(y-h/2)-10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        last_detected_time = time.time()
        if not is_recording:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            filename = f"color_trace_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
            h, w, _ = frame.shape
            out = cv2.VideoWriter(filename, fourcc, 20.0, (w, h))
            is_recording = True
            print(f"[@] 이벤트 녹화 시작: {filename}")

    # 3. 녹화 및 종료 로직
    if is_recording:
        out.write(annotated_frame)
        if time.time() - last_detected_time > wait_time:
            out.release()
            is_recording = False
            track_history.clear()
            track_colors.clear() # 상황 종료 시 색상 리스트도 초기화하여 메모리 절약
            print("[!] 상황 종료: 영상 저장 완료")

    cv2.imshow("Smart CCTV - Color Tracking", annotated_frame)
    if cv2.waitKey(1) & 0xFF == ord("q"): break

cap.release()
if out: out.release()
cv2.destroyAllWindows()