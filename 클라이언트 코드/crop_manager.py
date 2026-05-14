# crop_manager.py

import os
import cv2


class CropManager:
    def __init__(self, save_dir="outputs/crops", padding=20):
        """
        save_dir:
        - crop 이미지 저장 폴더

        padding:
        - bbox 주변을 조금 더 넓게 자르기 위한 여백
        """
        self.save_dir = save_dir
        self.padding = padding

        os.makedirs(self.save_dir, exist_ok=True)

    def crop_person(self, frame, bbox):
        """
        bbox 기준으로 사람 영역 crop
        """

        x1, y1, x2, y2 = bbox
        frame_height, frame_width = frame.shape[:2]

        # padding 적용 + 프레임 밖으로 나가지 않게 보정
        x1 = max(0, x1 - self.padding)
        y1 = max(0, y1 - self.padding)
        x2 = min(frame_width, x2 + self.padding)
        y2 = min(frame_height, y2 + self.padding)

        crop = frame[y1:y2, x1:x2]

        if crop.size == 0:
            return None

        return crop

    def save_crop(self, frame, bbox, person_id):
        """
        crop 이미지를 저장하고 저장 경로 반환
        """

        crop = self.crop_person(frame, bbox)

        if crop is None:
            return None

        filename = f"person_{person_id}.jpg"
        save_path = os.path.join(self.save_dir, filename)

        cv2.imwrite(save_path, crop)

        return save_path

    def save_crop_once(self, frame, bbox, person_id, saved_person_ids):
        """
        같은 person_id에 대해 crop을 한 번만 저장하고 싶을 때 사용

        saved_person_ids:
        - 이미 crop 저장한 person_id를 담는 set
        """

        if person_id in saved_person_ids:
            return None

        save_path = self.save_crop(frame, bbox, person_id)

        if save_path is not None:
            saved_person_ids.add(person_id)

        return save_path