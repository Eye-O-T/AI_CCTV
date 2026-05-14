# person_tracker.py

from ultralytics import YOLO


class PersonTracker:
    def __init__(
        self,
        model_path="yolo26s.pt",
        target_class="person",
        conf_threshold=0.5,
        tracker_config="bytetrack.yaml",
    ):
        self.model = YOLO(model_path)
        self.target_class = target_class
        self.conf_threshold = conf_threshold
        self.tracker_config = tracker_config

    def track(self, frame):
        """
        YOLO 추론 + 객체 추적 수행

        return:
        [
            {
                "person_id": 1,
                "bbox": (x1, y1, x2, y2),
                "conf": 0.87,
                "class_name": "person"
            }
        ]
        """

        results = self.model.track(
            frame,
            persist=True,
            tracker=self.tracker_config,
            verbose=False
        )

        tracked_persons = []

        if results is None or len(results) == 0:
            return tracked_persons

        result = results[0]

        if result.boxes is None:
            return tracked_persons

        for box in result.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            class_name = self.model.names[cls_id]

            if class_name != self.target_class:
                continue

            if conf < self.conf_threshold:
                continue

            # track_id가 없는 경우 방어 처리
            if box.id is None:
                continue

            person_id = int(box.id[0])

            x1, y1, x2, y2 = map(int, box.xyxy[0])

            tracked_persons.append({
                "person_id": person_id,
                "bbox": (x1, y1, x2, y2),
                "conf": conf,
                "class_name": class_name
            })

        return tracked_persons