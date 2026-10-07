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

    def __init__(self, parent=None, api_factory=CameraApiClient):
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
        self._stop_timer = QTimer(self)
        self._stop_timer.setInterval(100)
        self._stop_timer.timeout.connect(self._poll_stop)

    @property
    def busy(self):
        return bool(self.runs) or self.monitor_camera_id is not None

    @property
    def occupied_slots(self):
        return sum(run.slot is not None for run in self.runs.values())

    def bind_session(self, session):
        self.session = session

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
                clip_max_seconds=run.options.get('clip_max_seconds', 10))
        except Exception:
            self._status(run, '오류')
            self.notice.emit(camera_id, '영상 작업을 준비하지 못했습니다. 입력과 저장 경로를 확인하세요.')
            self._release(run)
            return
        for name in ('frame', 'metrics', 'event', 'loading', 'status'):
            getattr(run.worker, name + '_ready').connect(
                lambda value, r=run, kind=name: self._dispatch(r, kind, value))
        run.worker.finished.connect(lambda r=run: self._dispatch(r, 'finished', None))
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
                    self.notice.emit(camera_id, '작업 정리에 실패했습니다. 재실행·화면 전환을 중단했습니다.')
                continue
            self._status(run, '오류' if run.terminal_error else '중지됨')
            self._release(run)
            self.stopped.emit(camera_id)
        if not any(r.stopping and not r.cleanup_failed for r in self.runs.values()):
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
        if self._shutdown_started and not self.busy and not self._shutdown_emitted:
            self._shutdown_emitted = True
            self.shutdown_complete.emit()
