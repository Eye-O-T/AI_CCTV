import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import cv2
from PyQt5.QtCore import QThread, pyqtSignal

from detection.full_body_checker import FullBodyChecker
from detection.person_state_manager import PersonStateManager
from detection.person_tracker import PersonTracker
from recovery.network_recovery_manager import NetworkRecoveryManager
from storage.clip_manager import ClipManager
from storage.crop_manager import CropManager
from storage.recording_manager import RecordingManager
from video.video_stream import VideoStream
from workers.vlm_worker import VLMWorker


class VideoWorker(QThread):
    status_ready = pyqtSignal(str)
    frame_ready = pyqtSignal(object) # 분석이 끝난 프레임을 GUI 화면에 보내기
    metrics_ready = pyqtSignal(dict) # 현재 객체 수, 추적 중인 사람 수 보내기
    event_ready = pyqtSignal(dict) # 오류, 사라짐, VLM 큐 등록 같은 이벤트 보내기
    loading_ready = pyqtSignal(str) # START 이후 첫 화면이 뜨기 전 로딩 상태 보내기

    def __init__( # start누르면 실행
        self,
        source=0,
        use_yolo=True, # yolo 사용 여부
        use_vlm=False, # vlm사용여부
        ai_cctv_path="", # 녹화 폴더
        original_segment_seconds=10, # 녹화 간격
        clip_max_seconds=10, # 클립 최대 길이
        camera_id=1,
        edge_api_base_url=None
    ):
        super().__init__()
        self.camera_id = camera_id
        self.execution_id = uuid4().hex
        self.edge_api_base_url = edge_api_base_url
        self.source = source
        self.running = True
        self.use_yolo = use_yolo
        self.use_vlm = use_yolo and use_vlm

        # 클래스 연결
        self.stream = VideoStream(source=self.source)
        self.tracker = None
        self.full_body_checker = FullBodyChecker()
        # 사용자 미디어는 이동하지 않는다. 새 실행의 저장물만 분리한다.
        root = Path(ai_cctv_path).resolve() if ai_cctv_path else Path(__file__).resolve().parents[1] / 'outputs'
        self.run_directory = root / 'cameras' / f'camera_{camera_id}' / self.execution_id
        self.crop_manager = CropManager(save_dir=str(self.run_directory / 'crops'))
        self.state_manager = PersonStateManager(disappear_timeout=3.0)
        self.ai_cctv_path = str(self.run_directory) if ai_cctv_path else ''
        self.original_segment_seconds = original_segment_seconds
        self.clip_max_seconds = clip_max_seconds
        self.recording_manager = None
        self.clip_manager = None
        self.recovery_manager = None
        self.recovery_lock = threading.Lock()
        self.recovery_threads = []
        self.cleanup_ok = False
        self.cleanup_errors = []

        # vlm켜져있을때만 vlmworker만듦
        self.vlm_worker = None
        if self.use_yolo and self.use_vlm:
            self.vlm_worker = VLMWorker(self.state_manager)

    def disable_ai_pipeline(self, message):
        self.use_yolo = False
        self.use_vlm = False
        self.tracker = None

        if self.vlm_worker is not None:
            self.vlm_worker.request_stop()

        if self.clip_manager is not None:
            self.clip_manager.finish_all()
            self.clip_manager = None

        if hasattr(self.state_manager, "person_states"):
            self.state_manager.person_states.clear()

        self.event_ready.emit({
            "type": "error",
            "message": message
        })

    def run(self):
        try:
            self._run_video()
        except Exception:
            self.event_ready.emit({"type": "error", "message": "영상 처리 중 오류가 발생했습니다."})
        finally:
            self.running = False
            self._cleanup()

    def _run_video(self):
        if not self.running:
            return
        self.status_ready.emit("연결 중")
        self.loading_ready.emit("영상 스트림 연결 중...")

        # 스트림 열기
        if not self.stream.open():
            self.event_ready.emit({
                "type": "error",
                "message": "영상 스트림 열기 실패"
            })
            return

        # 저장경로 있으면 RecordingManager만들어 녹화하고 없으면 녹화 안함.
        if self.ai_cctv_path:
            fps = self.stream.get_fps() # 현재 영상 스트림에서 fps가져오기. 이게 있어야 녹화 정상적으로 가능

            self.recording_manager = RecordingManager(
                base_dir=self.ai_cctv_path,
                fps=fps,
                segment_seconds=self.original_segment_seconds
            )
            if self.use_yolo:
                self.clip_manager = ClipManager(
                    base_dir=self.ai_cctv_path,
                    fps=fps,
                    max_clip_seconds=self.clip_max_seconds,
                    disappear_timeout=3.0
                )

        if getattr(self.stream, "is_rtsp", False) and self.ai_cctv_path and self.edge_api_base_url:
            self.recovery_manager = NetworkRecoveryManager(
                camera_id=f"camera_{self.camera_id}",
                server_url=self.edge_api_base_url.rstrip("/") + "/recover",
                base_dir=self.ai_cctv_path,
                min_failure_seconds=2.0,
                request_timeout=60,
            )

        # vlm 켜져있을때만 vlmworker실행
        if self.running and self.use_yolo:
            try:
                self.loading_ready.emit("YOLO 모델 로딩 중...")
                self.tracker = PersonTracker(model_path="yolo26s.pt")
            except Exception as e:
                self.disable_ai_pipeline(
                    f"YOLO 초기화 실패: CCTV 모드로 전환합니다. ({e})"
                )

        if self.running and self.use_yolo and self.use_vlm and self.vlm_worker is not None:
            self.loading_ready.emit("VLM 모델 로딩 중...")
            self.vlm_worker.start()

            while self.running and not self.vlm_worker.wait_until_ready(timeout=0.1):
                if self.vlm_worker.has_failed():
                    error = self.vlm_worker.error_message or "알 수 없는 오류"
                    self.disable_ai_pipeline(
                        f"VLM 초기화 실패: CCTV 모드로 전환합니다. ({error})"
                    )
                    break

            if self.running and self.use_yolo and self.use_vlm:
                self.loading_ready.emit("실시간 화면 준비 중...")
        else:
            self.loading_ready.emit("실시간 화면 준비 중...")

        while self.running:
            ret, frame = self.stream.read()
            self.handle_rtsp_connection_events()

            if not ret:
                if getattr(self.stream, "is_rtsp", False):
                    # RTSP 모드에서는 일시적인 지연이나 재연결 중일 때 프레임이 없을 수 있으므로
                    # 바로 에러를 뿜지 않고 10ms 대기 후 루프를 계속 돕니다.
                    receiver = getattr(self.stream, 'receiver', None)
                    if receiver is not None and not receiver.is_connected:
                        if not getattr(self, '_waiting_notified', False):
                            self.status_ready.emit('연결 대기 / 재연결 중')
                            self._waiting_notified = True
                    time.sleep(0.01)
                    continue

                self.event_ready.emit({
                    "type": "error",
                    "message": "웹캠 프레임 수신 실패. 장치 확인 후 다시 시작하세요."
                })
                return
            self._waiting_notified = False
            # RecordingManager가 만들어져있으면 현재 프레임 저장
            # YOLO 바운딩박스 그려지기 전의 프레임 저장
            if self.recording_manager is not None:
                self.recording_manager.write_frame(frame)

            persons = []
            clip_frame = None
            if self.use_yolo and self.tracker is not None:
                try:
                    # 프레임에서 yolo분석, 객체 추적
                    persons = self.tracker.track(frame)
                    clip_frame = frame.copy()
                except Exception as e:
                    traceback.print_exc()
                    self.disable_ai_pipeline(
                        f"YOLO 추론 실패: CCTV 모드로 전환합니다. ({e})"
                    )
            """
            이렇게 반환되는데 인물 여러멍이면 리스트로 반환
            {
                "person_id": 1,
                "bbox": [x1, y1, x2, y2],
                "conf": 0.87
            },
            """

            for person in persons: # 사람마다 처리.
                person_id = person["person_id"]
                bbox = person["bbox"]
                conf = person["conf"]

                x1, y1, x2, y2 = map(int, bbox) # opencv로 박스 그리려면 정수로 바꿔야해서 int형으로 변환

                # 전신 검사 여부 체크
                is_full_body = self.full_body_checker.is_full_body_visible(
                    bbox,
                    frame.shape
                )

                # person_id 상태 업데이트
                self.state_manager.update_person(
                    person_id=person_id,
                    bbox=bbox,
                    is_full_body=is_full_body
                )

                crop_path = None

                # vlm켜져있고, 사람 전신 보이고, 해당 인물 crop이미지가 저장되어있지 않다면 crop저장
                if (
                    self.use_vlm
                    and is_full_body
                    and not self.state_manager.has_crop_saved(person_id)
                ):
                    crop_path = self.crop_manager.save_crop(
                        frame=frame,
                        bbox=bbox,
                        person_id=person_id
                    )

                    # 인물 crop상태 업데이트
                    if crop_path is not None:
                        self.state_manager.mark_crop_saved(person_id, crop_path)
                        # vlmworker작업큐에 crop이미지 추가(비동기 스레드 처리)
                        if self.vlm_worker is not None:
                            self.vlm_worker.add_task(person_id, crop_path)

                        # gui 이벤트 표시용
                        self.event_ready.emit({
                            "type": "vlm_queue",
                            "person_id": person_id,
                            "time": datetime.now().strftime("%H:%M:%S")
                        })

                if self.use_yolo and self.clip_manager is not None:
                    self.clip_manager.update_person(
                        person_id=person_id,
                        frame=clip_frame,
                        bbox=bbox,
                        crop_path=crop_path
                    )

                # 화면에 전신여부 체크용
                status = self.full_body_checker.get_status_text(
                    bbox,
                    frame.shape
                )
                # 바운딩박스 색깔 - 전신 : 초록, 전신x : 빨강
                color = (0, 255, 0) if is_full_body else (0, 0, 255)

                # 바운딩박스 그리기
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                # 사람 상태 가져와서 vlm 완료 여부 표시
                state = self.state_manager.get_state(person_id)
                vlm_text = ""
                if state is not None and state.get("vlm_done", False):
                    vlm_text = " VLM_DONE"

                label = f"ID:{person_id} {status} {conf:.2f}{vlm_text}"

                font_scale = 0.9
                font_thickness = 3
                text_size, baseline = cv2.getTextSize(
                    label,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    font_thickness,
                )
                label_x = max(0, x1)
                label_y = max(text_size[1] + 8, y1 - 10)
                bg_x2 = min(frame.shape[1], label_x + text_size[0] + 8)
                bg_y1 = max(0, label_y - text_size[1] - 8)
                bg_y2 = min(frame.shape[0], label_y + baseline + 4)

                cv2.rectangle(
                    frame,
                    (label_x, bg_y1),
                    (bg_x2, bg_y2),
                    (0, 0, 0),
                    -1,
                )
                cv2.putText(
                    frame,
                    label,
                    (label_x + 4, label_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    color,
                    font_thickness
                )
            # 사라진 사람 메모리에서 제거 후 업데이트
            removed_ids = []
            if self.use_yolo:
                removed_ids = self.state_manager.remove_disappeared_persons()
            for removed_id in removed_ids:
                if self.clip_manager is not None:
                    self.clip_manager.finish_person(removed_id)

                self.event_ready.emit({
                    "type": "disappear",
                    "person_id": removed_id,
                    "time": datetime.now().strftime("%H:%M:%S")
                })

            # 현재 PersonStateManager(상태관리자)에 남아있는 사람 수
            tracked_total = 0
            if hasattr(self.state_manager, "person_states"):
                tracked_total = len(self.state_manager.person_states)
            # GUI에 숫자 전송
            self.metrics_ready.emit({
                "current_objects": len(persons),
                "tracked_total": tracked_total
            })
            # 바운딩박스와 라벨 그려진 프레임 GUI로 전송
            # CCTVMainWindow.update_frame()에서 이 프레임 받아서 송출
            self.frame_ready.emit(frame)

    def _cleanup(self):
        # 이 메서드는 영상 작업 스레드에서 실행하므로 GUI를 기다리게 하지 않는다.
        # 각각의 정리는 독립적으로 시도하여 하나의 예외가 나머지 해제를 막지 않는다.
        actions = []
        if self.vlm_worker is not None:
            self.vlm_worker.request_stop()
        if self.recording_manager is not None:
            actions.append(self.recording_manager.stop_recording)
        if self.clip_manager is not None:
            actions.append(self.clip_manager.finish_all)
        for action in actions:
            try:
                action()
            except Exception:
                self.cleanup_errors.append("녹화 또는 클립 마무리 실패")
        try:
            while not self.stream.release():
                time.sleep(0.05)
        except Exception:
            self.cleanup_errors.append("영상 스트림 해제 실패")
        if self.vlm_worker is not None:
            while not self.vlm_worker.stop(timeout=0.2):
                pass
        for thread in self.recovery_threads:
            while thread.is_alive():
                thread.join(timeout=0.2)
        self.cleanup_ok = not self.cleanup_errors

    def handle_rtsp_connection_events(self):
        if not self.running:
            return
        for event in self.stream.pop_connection_events():
            event_type = event.get("type")

            if event_type == "failure":
                if self.recovery_manager is None:
                    result = {
                        "started": True,
                        "failure_start_time": event.get("failure_start_time"),
                    }
                else:
                    result = self.recovery_manager.record_failure(
                        event.get("failure_start_time")
                    )

                if result.get("started"):
                    if self.recording_manager is not None:
                        self.recording_manager.stop_recording()

                    self.event_ready.emit({
                        "type": "network_failure",
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "message": (
                            "네트워크 장애 감지: "
                            f"{result.get('failure_start_time')}"
                        ),
                    })

            elif event_type == "recovery":
                if self.recovery_manager is None:
                    self.event_ready.emit({
                        "type": "network_recovered",
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "message": "네트워크 연결 복구",
                    })
                else:
                    thread = threading.Thread(
                        target=self._run_recovery_request,
                        args=(event,),
                        daemon=True,
                    )
                    self.recovery_threads = [item for item in self.recovery_threads if item.is_alive()]
                    self.recovery_threads.append(thread)
                    thread.start()

    def _run_recovery_request(self, event):
        if self.recovery_manager is None:
            return

        with self.recovery_lock:
            if not self.running:
                return
            self.recovery_manager.record_failure(
                event.get("failure_start_time")
            )
            result = self.recovery_manager.record_recovery(
                event.get("recovered_time")
            )

        if result.get("success"):
            if result.get("skipped"):
                self.event_ready.emit({
                    "type": "network_recovered",
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "message": "네트워크 연결 복구",
                })
                return

            self.event_ready.emit({
                "type": "network_recovered",
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": (
                    "장애 복구 영상 저장 완료: "
                    f"{result.get('file_path')}"
                ),
            })
        else:
            self.event_ready.emit({
                "type": "error",
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": (
                    "장애 복구 영상 저장 실패. 장치 API 연결과 저장 경로를 확인하세요."
                ),
            })

    def stop(self):
        self.request_stop()

    def request_stop(self):
        self.running = False
        if self.vlm_worker is not None:
            self.vlm_worker.request_stop()
