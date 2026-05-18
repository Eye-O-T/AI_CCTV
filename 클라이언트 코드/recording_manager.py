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

        self.recording_dir = os.path.join(
            self.base_dir,
            "원본 녹화본"
        )

        os.makedirs(self.recording_dir, exist_ok=True)

        self.start_time_str = None
        self.temp_save_path = None

    def start_recording(self, frame_size):
        self.frame_size = frame_size

        start_time = datetime.now()

        self.start_time_str = start_time.strftime("%Y-%m-%d_%H-%M-%S")

        temp_filename = f"recording_{self.start_time_str}.mp4"

        self.temp_save_path = os.path.join(
            self.recording_dir,
            temp_filename
        )

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        self.writer = cv2.VideoWriter(
            self.temp_save_path,
            fourcc,
            self.fps,
            self.frame_size
        )

        if not self.writer.isOpened():
            print("원본 영상 저장 Writer 생성 실패")
            self.writer = None
            return False

        print(f"원본 영상 저장 시작: {self.temp_save_path}")

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

            end_time_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            final_filename = f"{self.start_time_str}~{end_time_str}.mp4"

            final_save_path = os.path.join(
                self.recording_dir,
                final_filename
            )

            try:
                os.rename(
                    self.temp_save_path,
                    final_save_path
                )

                print(f"원본 영상 저장 종료: {final_save_path}")

            except Exception as e:
                print(f"파일 이름 변경 실패: {e}")