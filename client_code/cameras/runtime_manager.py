"""GUI 스레드에서 최대 두 카메라의 요청·실행·비동기 종료를 조정한다."""
from dataclasses import dataclass
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from cameras.api_client import CameraApiClient, valid_camera


@dataclass
class CameraRun:
    camera: dict
    options: dict
    generation: int
    slot: int | None
    api: object = None
    worker: object = None
    pending: bool = True
    stopping: bool = False
    terminal_error: bool = False
    cleanup_failed: bool = False
    stop_started: float = 0


class CameraRuntimeManager(QObject):
    changed = pyqtSignal()
    detail_failed = pyqtSignal(int, str)
    camera_updated = pyqtSignal(dict)
    started = pyqtSignal(dict)
    monitor_ready = pyqtSignal(dict)
    status_changed = pyqtSignal(dict, str)
    frame_ready = pyqtSignal(int, object)
    metrics_ready = pyqtSignal(int, dict)
    event_ready = pyqtSignal(int, dict)
    loading_ready = pyqtSignal(int, str)
    notice = pyqtSignal(int, str)
    stop_requested = pyqtSignal(int)
    stopped = pyqtSignal(int)
    shutdown_complete = pyqtSignal()
    global_person_ready = pyqtSignal(int, object)

    def __init__(self, parent=None, api_factory=CameraApiClient, reid_factory=None):
        super().__init__(parent)
        self.session = None
        self.api_factory = api_factory
        self.runs = {}
        self.slots = [None, None]
        self.runtime_states = {}
        self.shutting_down = False
        self.monitor_camera_id = None
        self._generation = 0
        self._shutdown_emitted = False
        self._shutdown_started = False
        self.reid_pipeline = None
        self.reid_factory = reid_factory
        self._reid_timer = QTimer(self)
        self._reid_timer.setInterval(100)
        self._reid_timer.timeout.connect(self._poll_reid)
        self._stop_timer = QTimer(self)
        self._stop_timer.setInterval(100)
        self._stop_timer.timeout.connect(self._poll_stop)

    @property
    def busy(self):
        return (bool(self.runs) or self.monitor_camera_id is not None
                or bool(self._shutdown_started and self.reid_pipeline is not None and self.reid_pipeline.is_alive()))

    @property
    def occupied_slots(self):
        return sum(run.slot is not None for run in self.runs.values())

    def bind_session(self, session):
        self.session = session
        if hasattr(session, 'refresh_completed'):
            session.refresh_completed.connect(self._refresh_reid_token)
        if hasattr(session, 'expired'):
            session.expired.connect(self._stop_reid)
        if hasattr(session, 'signed_out'):
            session.signed_out.connect(self._stop_reid)

    def _ensure_reid(self):
        if self.reid_pipeline is not None:
            return self.reid_pipeline
        token = getattr(self.session, 'access_token', None)
        base_url = getattr(getattr(self.session, 'api', None), 'base_url', None)
        if not isinstance(token, str) or not token or not isinstance(base_url, str):
            return None
        factory = self.reid_factory
        if factory is None:
            from reid.pipeline import ReIDPipeline
            factory = ReIDPipeline
        self.reid_pipeline = factory(base_url, token)
        self._reid_timer.start()
        return self.reid_pipeline

    def _refresh_reid_token(self, error):
        if not error and self.reid_pipeline is not None and not self.shutting_down:
            self.reid_pipeline.set_access_token(self.session.access_token)

    def _stop_reid(self, *args):
        if self.reid_pipeline is not None:
            self.reid_pipeline.request_stop()

    def retry_reid(self):
        return self.reid_pipeline is not None and self.reid_pipeline.retry_pending()

    def _poll_reid(self):
        if self.reid_pipeline is None:
            return
        for camera_id, code in self.reid_pipeline.take_notices():
            if code == 'INVALID_ACCESS_TOKEN' and not self.shutting_down:
                self.session.expire_access()
                continue
            if code.startswith('WEIGHTS_') or code in ('MODEL_LOAD_FAILED', 'REID_INITIALIZATION_FAILED'):
                message = '글로벌 인물 모델을 준비하지 못했습니다. 모델 파일을 확인하세요. 영상은 유지합니다.'
            elif code in ('IMAGE_READ_FAILED', 'INFERENCE_FAILED', 'INVALID_FEATURES'):
                message = '인물 특징 추출에 실패한 관측이 있습니다. 영상은 유지합니다.'
            elif code == 'RESULT_QUEUE_FULL':
                message = '글로벌 ID 결과 수신이 지연되어 일부 관측을 제외했습니다.'
            else:
                message = '글로벌 ID 처리를 확인하지 못했습니다. 영상은 유지하며 로그아웃 후 다시 로그인하면 작업을 다시 시작합니다.'
            targets = [camera_id] if camera_id is not None else list(self.runs)
            for target in targets:
                if target in self.runs:
                    self.notice.emit(target, message)
        if self.shutting_down and not self.reid_pipeline.is_alive():
            self._reid_timer.stop()
            self._check_shutdown()

    def can_start(self, camera):
        return bool(self.session is not None and not self.shutting_down and camera
                    and camera['is_enabled'] and camera['camera_id'] not in self.runs
                    and camera['camera_id'] != self.monitor_camera_id
                    and self.occupied_slots < 2)

    def request_detail(self, camera, purpose, options=None):
        if purpose == 'monitor':
            return self._request_monitor(camera)
        if purpose != 'start' or not self.can_start(camera):
            return False
        camera_id = camera['camera_id']
        # 중지된 칸만 재사용한다. 다른 실행의 위치는 이동하지 않는다.
        occupied = {run.slot for run in self.runs.values() if run.slot is not None}
        available = [i for i in range(2) if i not in occupied]
        previous = self.slots.index(camera_id) if camera_id in self.slots else None
        slot = previous if previous in available else available[0]
        self.slots[slot] = camera_id
        return self._request(camera, slot, options or {})

    def _request_monitor(self, camera):
        if (self.session is None or self.shutting_down or self.monitor_camera_id is not None
                or not camera or not camera['is_enabled']):
            return False
        camera_id = camera['camera_id']
        run = self.runs.get(camera_id)
        if run and (run.pending or run.stopping):
            return False
        self.monitor_camera_id = camera_id
        if run:
            self.monitor_ready.emit(dict(run.camera))
            self.changed.emit()
            return True
        return self._request(camera, None, {})

    def _request(self, camera, slot, options):
        camera_id = camera['camera_id']
        self._generation += 1
        run = CameraRun(dict(camera), dict(options), self._generation, slot)
        self.runs[camera_id] = run
        try:
            run.api = self.api_factory(self.session, self)
            run.api.completed.connect(lambda p, e, f, r=run: self._detail_received(r, p, e, f))
            if slot is not None:
                self._status(run, '정보 확인 중')
            self.changed.emit()
            self.notice.emit(camera_id, '최신 카메라 정보를 확인하고 있습니다…')
            accepted = run.api.request('GET', f'/{camera_id}')
        except Exception:
            accepted = False
        if not accepted and self._current(run):
            self._detail_failed(run, 'CONNECTION_ERROR')
        return accepted

    def _current(self, run):
        return self.runs.get(run.camera['camera_id']) is run

    def _detail_failed(self, run, error):
        self._status(run, '조회 실패')
        self.detail_failed.emit(run.camera['camera_id'], error)
        if run.slot is None:
            self.monitor_camera_id = None
        self._release(run)

    def _detail_received(self, run, payload, error, field):
        if not self._current(run) or not run.pending or run.stopping or self.shutting_down:
            return
        run.pending = False
        camera_id = run.camera['camera_id']
        if error or not valid_camera(payload, detail=True) or payload['camera_id'] != camera_id:
            self._detail_failed(run, error or 'INVALID_RESPONSE')
            return
        run.camera = dict(payload)
        self.camera_updated.emit(dict(payload))
        if not payload['is_enabled']:
            self._status(run, '비활성')
            self.notice.emit(camera_id, '비활성 카메라는 실행할 수 없습니다.')
            if run.slot is None:
                self.monitor_camera_id = None
            self._release(run)
        elif run.slot is None:
            self.monitor_ready.emit(dict(payload))
            self._release(run)
        else:
            self._start(run)

    def _start(self, run):
        camera = run.camera
        camera_id = camera['camera_id']
        try:
            from workers.video_worker import VideoWorker
            run.worker = VideoWorker(
                source=camera['webcam_index'] if camera['camera_type'] == 'webcam' else camera['rtsp_url'],
                camera_id=camera_id, edge_api_base_url=camera.get('edge_api_base_url'),
                use_yolo=run.options.get('use_yolo', True), use_vlm=False,
                ai_cctv_path=run.options.get('ai_cctv_path', ''),
                original_segment_seconds=run.options.get('original_segment_seconds', 10),
                clip_max_seconds=run.options.get('clip_max_seconds', 10),
                reid_pipeline=self._ensure_reid() if run.options.get('use_yolo', True) else None)
        except Exception:
            self._status(run, '오류')
            self.notice.emit(camera_id, '영상 작업을 준비하지 못했습니다. 입력과 저장 경로를 확인하세요.')
            self._release(run)
            return
        for name in ('frame', 'metrics', 'event', 'loading', 'status'):
            getattr(run.worker, name + '_ready').connect(
                lambda value, r=run, kind=name: self._dispatch(r, kind, value))
        run.worker.finished.connect(lambda r=run: self._dispatch(r, 'finished', None))
        if hasattr(run.worker, 'reid_ready'):
            run.worker.reid_ready.connect(lambda result, r=run: self._dispatch(r, 'reid', result))
        self.started.emit(dict(camera))
        self._status(run, '로딩 중')
        self.loading_ready.emit(camera_id, '시스템 시작 중…')
        self.changed.emit()
        try:
            run.worker.start()
        except Exception:
            # 시작 예외도 안전한 정리가 확인되기 전에는 슬롯을 반환하지 않는다.
            run.terminal_error = True
            self.stop(camera_id)

    def _dispatch(self, run, kind, value):
        if not self._current(run) or run.stopping or self.shutting_down:
            return
        camera_id = run.camera['camera_id']
        if kind == 'frame':
            self._status(run, 'LIVE')
            self.frame_ready.emit(camera_id, value)
        elif kind == 'status':
            self._status(run, value)
        elif kind == 'finished':
            run.terminal_error = True
            self.stop(camera_id)
        elif kind == 'event':
            if value.get('type') == 'network_failure':
                self._status(run, '연결 장애 / 재연결 중')
            self.event_ready.emit(camera_id, value)
        elif kind == 'reid':
            if run.worker.is_reid_result_current(value):
                self.global_person_ready.emit(camera_id, value)
        else:
            getattr(self, kind + '_ready').emit(camera_id, value)

    def _status(self, run, status):
        camera_id = run.camera['camera_id']
        if self.runtime_states.get(camera_id) != status:
            self.runtime_states[camera_id] = status
            self.status_changed.emit(dict(run.camera), status)

    def stop(self, camera_id):
        run = self.runs.get(camera_id)
        if run is None:
            if self.monitor_camera_id == camera_id:
                self.stop_requested.emit(camera_id)
            return
        if run.stopping:
            return
        run.stopping = True
        run.pending = False
        run.stop_started = time.monotonic()
        if run.api is not None:
            run.api.cancel()
        self._status(run, '중지 중')
        self.changed.emit()
        self.stop_requested.emit(camera_id)
        if run.worker is not None:
            try:
                run.worker.request_stop()
            except Exception:
                run.cleanup_failed = True
        self.notice.emit(camera_id, '영상 수신·녹화·분석을 정리하고 있습니다…')
        self._stop_timer.start()

    def monitor_closed(self):
        self.monitor_camera_id = None
        self.changed.emit()
        self._check_shutdown()

    def begin_shutdown(self):
        self.shutting_down = True
        self._shutdown_started = True
        for camera_id in list(self.runs):
            self.stop(camera_id)
        self._stop_reid()
        if self.monitor_camera_id is not None:
            self.stop_requested.emit(self.monitor_camera_id)
        self.changed.emit()
        # 빈 실행도 GUI가 종료 준비를 마친 뒤 완료를 통지한다.
        QTimer.singleShot(0, self._check_shutdown)

    def _poll_stop(self):
        for run in list(self.runs.values()):
            if not run.stopping:
                continue
            camera_id = run.camera['camera_id']
            if (run.worker is not None and run.worker.isRunning()) or self.monitor_camera_id == camera_id:
                if time.monotonic() - run.stop_started > 30:
                    self.notice.emit(camera_id, '정리에 시간이 걸리고 있습니다. 작업 종료를 기다리는 중입니다.')
                continue
            if run.cleanup_failed or (run.worker is not None and not run.worker.cleanup_ok):
                run.cleanup_failed = True
                if self.runtime_states.get(camera_id) != '정리 실패':
                    self._status(run, '정리 실패')
                    storage_errors = getattr(run.worker, 'storage_errors', [])
                    reason = storage_errors[-1] + ' ' if storage_errors else ''
                    self.notice.emit(camera_id, reason + '작업 정리에 실패했습니다. 재실행·화면 전환을 중단했습니다.')
                continue
            self._status(run, '오류' if run.terminal_error else '중지됨')
            storage_errors = getattr(run.worker, 'storage_errors', [])
            if storage_errors:
                self.notice.emit(camera_id, storage_errors[-1])
            self._release(run)
            self.stopped.emit(camera_id)
        if (not any(r.stopping and not r.cleanup_failed for r in self.runs.values())
                and not (self._shutdown_started and self.reid_pipeline is not None and self.reid_pipeline.is_alive())):
            self._stop_timer.stop()
        self._check_shutdown()

    def _release(self, run):
        if not self._current(run):
            return
        del self.runs[run.camera['camera_id']]
        if run.api is not None:
            run.api.cancel()
            run.api.deleteLater()
        if run.worker is not None:
            run.worker.deleteLater()
        self.changed.emit()

    def _check_shutdown(self):
        if (self._shutdown_started and not self.busy and not self._shutdown_emitted
                and not (self.reid_pipeline is not None and self.reid_pipeline.is_alive())):
            self._shutdown_emitted = True
            self.shutdown_complete.emit()
