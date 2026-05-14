# video_stream.py

import cv2


class VideoStream:
    def __init__(self, source=0):
        """
        source:
        - 웹캠: 0
        - RTSP: "rtsp://192.168.10.2:8554/stream"
        """
        self.source = source
        self.cap = None

    def open(self):
        self.cap = cv2.VideoCapture(self.source)

        if not self.cap.isOpened():
            print("영상 스트림 연결 실패")
            return False

        print("영상 스트림 연결 성공")
        return True

    def read(self):
        """
        프레임 1장 읽기
        return:
        - ret: 성공 여부
        - frame: 이미지 프레임
        """
        if self.cap is None:
            return False, None

        return self.cap.read()

    def get_fps(self):
        if self.cap is None:
            return 30

        fps = self.cap.get(cv2.CAP_PROP_FPS)

        if fps <= 0:
            return 30

        return fps

    def get_frame_size(self):
        if self.cap is None:
            return 640, 480

        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        return width, height

    def release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None