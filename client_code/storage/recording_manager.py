# recording_manager.py

import os
import cv2
from datetime import datetime, timedelta
from pathlib import Path


DEFAULT_TEMP_ROOT = Path(__file__).resolve().parents[2] / 'temp'


class RecordingManager:
    def __init__(
        self,
        base_dir,
        fps=30,
        frame_size=None,
        segment_seconds=60, # 몇초단위로 영상 저장할건지
        target_bitrate_kbps=2500,
        camera_id=1,
        temp_root=None,
    ):
        self.base_dir = base_dir # 녹화본 저장 경로
        self.fps = fps
        self.frame_size = frame_size
        self.segment_seconds = segment_seconds
        self.target_bitrate_kbps = target_bitrate_kbps
        self.camera_id = camera_id
        self.temp_root = (Path(temp_root).resolve() if temp_root is not None
                          else DEFAULT_TEMP_ROOT)
        self.temp_directory = None
        self.pending_move = False

        self.writer = None # 실제로 mp4 파일에 프레임을 쓰는 객체

        self.recording_dir = os.path.join( # 기본 경로 아래에 원본 녹화본 폴더를 만들겠다는 뜻
            self.base_dir,
            "원본 녹화본"
        )

        os.makedirs(self.recording_dir, exist_ok=True)

        self.start_time = None
        self.start_time_str = None # 녹화 시작시간 파일 이름 형식에 맞게 변환
        self.temp_save_path = None # 임시 파일 저장 경로
        self.next_frame_time = None
        self.frame_interval = timedelta(seconds=1.0 / self.fps)
        self.frames_written = 0

    def start_recording(self, frame_size): # 새로운 mp4파일 저장하는 함수
        if self.pending_move:
            raise OSError("이동에 실패한 원본 영상이 temp에 남아 있습니다.")
        self.frame_size = frame_size # 현재 프레임의 크기 저장

        self.start_time = datetime.now()
        self.start_time_str = self.start_time.strftime("%Y-%m-%d_%H-%M-%S_%f") # 같은 초의 재연결 파일도 분리
        self.next_frame_time = self.start_time
        self.frames_written = 0

        # UUID 대신 시각과 카메라로 구분한다. mkdir의 배타적 생성으로 동시 실행도 격리한다.
        parent = self.temp_root / self.start_time.strftime('%Y-%m-%d') / f'camera_{self.camera_id}'
        parent.mkdir(parents=True, exist_ok=True)
        label = self.start_time.strftime('원본_%H시%M분%S초_%f')
        sequence = 1
        while True:
            directory = parent / (label if sequence == 1 else f'{label}_{sequence}')
            try:
                directory.mkdir()
                break
            except FileExistsError:
                sequence += 1
        self.temp_directory = directory
        self.temp_save_path = str(directory / 'recording.mp4')

        # 저장 코덱 설정
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        # VideoWriter객체 생성
        self.writer = cv2.VideoWriter(
            self.temp_save_path, # 저장 경로
            fourcc, # 코덱
            self.fps, # FPS
            self.frame_size # 프레임 크기
        )
        self._apply_writer_profile(self.writer)

        # VideoWriter가 제대로 열리지 않았으면 실패 처리. 성공하면 함수 True반환
        if not self.writer.isOpened():
            self.writer.release()
            self.writer = None
            raise OSError("원본 영상 저장 Writer 생성 실패")

        print(
            f"원본 영상 저장 시작: {self.temp_save_path} "
            f"({self.frame_size[0]}x{self.frame_size[1]}, {self.fps:.1f}fps, "
            f"목표 {self.target_bitrate_kbps}kbps급)"
        )

        return True

    def _apply_writer_profile(self, writer):
        if writer is None:
            return

        if hasattr(cv2, "VIDEOWRITER_PROP_QUALITY"):
            writer.set(cv2.VIDEOWRITER_PROP_QUALITY, 75)

    # 프레임 하나를 영상파일에 저장하는 함수
    # 메인 루프에서 매 프레임마다 호출. 프레임 없으면 저장할 게 없으니 종료
    def write_frame(self, frame):
        if frame is None:
            return

        # 프레임 크기 계산
        height, width = frame.shape[:2]
        current_frame_size = (width, height)

        # 아직 저장 중인 mp4 파일이 없으면 현재 프레임 크기로 새 mp4 파일을 만들기. (초기에는 writer가 없으니)
        if self.writer is None:
            self.start_recording(current_frame_size)

        # start_recording()을 호출했는데도 실패하면 프레임 저장 포기하고 종료
        if self.writer is None:
            return

        # 녹화 경과 시간 계산
        elapsed_seconds = (datetime.now() - self.start_time).total_seconds()

        # 지정한 segment시간 경과하면 파일 이름을 시작시간~종료시간.mp4로 변경하고 새로운 임시파일에 저장 시작
        if elapsed_seconds >= self.segment_seconds:
            self.stop_recording()
            self.start_recording(current_frame_size)

        self._write_frame_by_wall_clock(frame, datetime.now())

    def _write_frame_by_wall_clock(self, frame, now):
        if self.writer is None or self.next_frame_time is None:
            return

        writes = 0
        max_writes_per_input = max(1, int(self.fps * 2))

        while self.next_frame_time <= now and writes < max_writes_per_input:
            self.writer.write(frame)
            self.next_frame_time += self.frame_interval
            self.frames_written += 1
            writes += 1

        if writes == max_writes_per_input and self.next_frame_time <= now:
            self.next_frame_time = now + self.frame_interval
            print("원본 영상 프레임 보정 한도 초과: 긴 지연 구간을 건너뜁니다.")

    # 현재 저장 중인 영상 파일을 종료하는 함수
    def stop_recording(self):
        if self.pending_move:
            raise OSError("완료된 원본 영상의 이동 실패: temp 파일을 보존합니다.")
        # 저장 중인 파일이 없으면 할 일이 없으니까 바로 종료
        if self.writer is None:
            return

        end_time = datetime.now()
        wall_seconds = (end_time - self.start_time).total_seconds()
        encoded_seconds = self.frames_written / self.fps if self.fps else 0
        effective_fps = self.frames_written / wall_seconds if wall_seconds > 0 else 0

        # release를 호출해서 writer닫기. 안하면 파일 깨지거나 정상 재생 안됨.
        self.writer.release()
        self.writer = None
        self.pending_move = True

        # 종료시간 문자열 만들기(파일 이름용)
        end_time_str = end_time.strftime("%H시%M분%S초")
        if end_time.date() != self.start_time.date():
            end_time_str = end_time.strftime("%Y-%m-%d_%H시%M분%S초")

        # 임시파일 이름을 시작시간~종료시간으로 바꿔서 저장
        stem = f"{self.start_time.strftime('%H시%M분%S초')}~{end_time_str}"

        # 최종 저장 경로 만들기
        destination = Path(self.recording_dir) / self.start_time.strftime('%Y-%m-%d') / f'camera_{self.camera_id}'

        # 파일 이름 변경중 문제 발생시 예외처리(권한 문제, 경로 문제등등)
        try:
            destination.mkdir(parents=True, exist_ok=True)
            sequence = 1
            while True:
                suffix = '' if sequence == 1 else f'_{sequence}'
                final_save_path = destination / f'{stem}{suffix}.mp4'
                if final_save_path.exists():
                    sequence += 1
                    continue
                try:
                    # Windows rename은 기존 파일을 덮어쓰지 않는다. 다른 볼륨이면 실패하며 복사하지 않는다.
                    os.rename(self.temp_save_path, final_save_path)
                    break
                except FileExistsError:
                    sequence += 1

            print(
                "원본 영상 저장 종료: "
                f"{final_save_path} "
                f"(실시간 {wall_seconds:.2f}s, 재생예상 {encoded_seconds:.2f}s, "
                f"저장프레임 {self.frames_written}, 실효FPS {effective_fps:.2f})"
            )

        except Exception as e:
            raise OSError("원본 영상 이동 실패: 프로젝트 temp에 파일을 보존합니다.") from e

        self.pending_move = False
        # 이번 구간이 만든 빈 폴더만 정리한다. 실패해도 완료된 영상 이동을 취소하지 않는다.
        try:
            self.temp_directory.rmdir()
        except OSError:
            pass

        # 녹화 끝나면 관련 정보 초기화
        self.start_time = None
        self.start_time_str = None
        self.temp_save_path = None
        self.next_frame_time = None
        self.frames_written = 0
