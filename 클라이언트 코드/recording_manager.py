# recording_manager.py

import os
import cv2
from datetime import datetime


class RecordingManager:
    def __init__(self, base_dir, fps=20, frame_size=None):
        self.base_dir = base_dir
        self.fps = fps
        self.frame_size = frame_size

        self.writer = None
        self.recording_dir = os.path.join(self.base_dir, "원본 녹화본")

        os.makedirs(self.recording_dir, exist_ok=True)

    def start_recording(self, frame_size):
        self.frame_size = frame_size

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"original_{now}.mp4"
        save_path = os.path.join(self.recording_dir, filename)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        self.writer = cv2.VideoWriter(
            save_path,
            fourcc,
            self.fps,
            self.frame_size
        )

        if not self.writer.isOpened():
            print("원본 영상 저장 Writer 생성 실패")
            self.writer = None
            return False

        print(f"원본 영상 저장 시작: {save_path}")
        return True

    def write_frame(self, frame):
        if frame is None:
            return

        height, width = frame.shape[:2]
        current_frame_size = (width, height)

        if self.writer is None:
            self.start_recording(current_frame_size)

        if self.writer is not None:
            self.writer.write(frame)

    def stop_recording(self):
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            print("원본 영상 저장 종료")