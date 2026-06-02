# video_stream.py

import cv2
import time
from rtsp_receiver import RTSPReceiver


class VideoStream:
    def __init__(self, source=0):
        self.source = source
        self.cap = None
        self.receiver = None
        
        # 입력 소스가 rtsp:// 로 시작하는 문자열인지 확인
        self.is_rtsp = isinstance(self.source, str) and self.source.lower().startswith("rtsp://")
        
        # RTSP 모드에서 프레임 중복 방지 및 동기화를 위한 마지막 읽은 프레임 타임스탬프
        self.last_read_frame_time = None

    def open(self):
        if self.is_rtsp:
            print(f"[VideoStream] RTSP 모드 활성화 - 소스: {self.source}")
            self.receiver = RTSPReceiver(rtsp_url=self.source, reconnect_interval=3)
            self.receiver.start()
            return True
        else:
            self.cap = cv2.VideoCapture(self.source)
            if not self.cap.isOpened():
                print("영상 스트림 연결 실패")
                return False
            print("영상 스트림 연결 성공")
            return True

    def read(self):
        if self.is_rtsp:
            if self.receiver is None:
                return False, None
            
            # 새로운 프레임이 수신될 때까지 미세 대기 (최대 100ms)하여 CPU 공회전 방지 및 동기화 수행
            start_wait = time.time()
            while True:
                # 수신기의 최신 프레임 갱신 시점 확인
                last_time = getattr(self.receiver, "last_frame_time", 0)
                
                # 프레임이 존재하고, 이전 읽은 시점보다 새로운 프레임인 경우 루프 탈출
                if self.receiver.frame is not None and (self.last_read_frame_time is None or last_time > self.last_read_frame_time):
                    break
                
                # 5ms 대기 후 재검사
                time.sleep(0.005)
                
                # 연결이 완전히 끊겼거나 100ms 대기 시 타임아웃으로 탈출
                if not self.receiver.is_connected or (time.time() - start_wait > 0.1):
                    break
            
            current_frame_time = getattr(self.receiver, "last_frame_time", 0)
            if (
                self.last_read_frame_time is not None
                and current_frame_time <= self.last_read_frame_time
            ):
                return False, None

            frame = self.receiver.get_frame()
            if frame is None:
                return False, None
            
            self.last_read_frame_time = current_frame_time
            return True, frame
        else:
            if self.cap is None:
                return False, None
            return self.cap.read()

    def get_fps(self):
        if self.is_rtsp:
            # RTSP 수신기 내 OpenCV cap 객체에서 FPS 획득 시도
            fps = 30
            if self.receiver is not None:
                with self.receiver.lock:
                    if self.receiver.cap is not None:
                        fps = self.receiver.cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                return 30
            return fps
        else:
            if self.cap is None:
                return 30
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                return 30
            return fps

    def get_frame_size(self):
        if self.is_rtsp:
            # 수신된 최신 프레임의 크기를 직접 분석
            if self.receiver is not None:
                frame = self.receiver.get_frame()
                if frame is not None:
                    h, w = frame.shape[:2]
                    return w, h
            return 640, 480
        else:
            if self.cap is None:
                return 640, 480
            width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            return width, height

    def pop_connection_events(self):
        if self.is_rtsp and self.receiver is not None:
            return self.receiver.pop_connection_events()
        return []

    def release(self):
        if self.is_rtsp:
            if self.receiver is not None:
                self.receiver.stop()
                self.receiver = None
        else:
            if self.cap is not None:
                self.cap.release()
                self.cap = None

