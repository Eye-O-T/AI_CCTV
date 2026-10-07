"""GUI 스레드에서 단일 카메라의 조회·실행·비동기 정리를 관리한다.

1단계는 동시 한 실행만 허용한다. 화면 선택과 Worker 수명은 분리한다.
"""
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from cameras.api_client import CameraApiClient, valid_camera


class CameraRuntimeManager(QObject):
    changed = pyqtSignal()
    detail_failed = pyqtSignal(str)
    camera_updated = pyqtSignal(dict)
    started = pyqtSignal(dict)
    monitor_ready = pyqtSignal(dict)
    status_changed = pyqtSignal(dict, str)
    frame_ready = pyqtSignal(object)
    metrics_ready = pyqtSignal(dict)
    event_ready = pyqtSignal(dict)
    loading_ready = pyqtSignal(str)
    notice = pyqtSignal(str)
    stop_requested = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.detail_api = None
        self.detail_pending = None
        self.worker = None
        self.running_camera = None
        self.runtime_states = {}
        self.run_generation = 0
        self.stopping = False
        self.shutting_down = False
        self.monitor_pending = False
        self._terminal_error = False
        self._stop_started = 0
        self._stop_timer = QTimer(self)
        self._stop_timer.setInterval(100)
        self._stop_timer.timeout.connect(self._poll_stop)

    @property
    def busy(self):
        return self.worker is not None or self.detail_pending is not None or self.stopping

    def bind_session(self, session):
        self.detail_api = CameraApiClient(session, self)
        self.detail_api.completed.connect(self.camera_detail_received)

    def request_detail(self, camera, purpose, options=None):
        if (self.detail_api is None or self.busy or self.shutting_down
                or self.monitor_pending or not camera or not camera['is_enabled']):
            return False
        # 조회 시작 시 설정을 복사하여 호출자 변경의 영향을 받지 않는다.
        self._terminal_error = False
        self._terminal_error = False
        self.detail_pending = (purpose, dict(camera), dict(options or {}))
        self.changed.emit()
        self.notice.emit('최신 카메라 정보를 확인하고 있습니다…')
        if not self.detail_api.request('GET', f"/{camera['camera_id']}"):
            self.detail_pending = None
            self.changed.emit()
            return False
        return True

    def camera_detail_received(self, payload, error, field):
        pending, self.detail_pending = self.detail_pending, None
        if pending is None or self.stopping or self.shutting_down:
            return
        purpose, camera, options = pending
        if error or not valid_camera(payload, detail=True) or payload['camera_id'] != camera['camera_id']:
            self.detail_failed.emit(error or 'INVALID_RESPONSE')
            self.changed.emit()
            return
        self.camera_updated.emit(dict(payload))
        if not payload['is_enabled']:
            self._set_status(payload, '비활성')
            self.notice.emit('비활성 카메라는 실행할 수 없습니다.')
        elif purpose == 'monitor':
            self.monitor_ready.emit(dict(payload))
        else:
            self._start_camera(payload, options)
        self.changed.emit()

    def _set_status(self, camera, status):
        if camera is None or self.runtime_states.get(camera['camera_id']) == status:
            return
        self.runtime_states[camera['camera_id']] = status
        self.status_changed.emit(dict(camera), status)

    def _dispatch_video(self, generation, callback, *args):
        if (generation == self.run_generation and self.worker is not None
                and not self.stopping and not self.shutting_down):
            callback(*args)

    def _start_camera(self, camera, options):
        self.running_camera = dict(camera)
        self.run_generation += 1
        generation = self.run_generation
        self._terminal_error = False
        try:
            from workers.video_worker import VideoWorker
            self.worker = VideoWorker(
                source=camera['webcam_index'] if camera['camera_type'] == 'webcam' else camera['rtsp_url'],
                camera_id=camera['camera_id'], edge_api_base_url=camera.get('edge_api_base_url'),
                use_yolo=options.get('use_yolo', True), use_vlm=False,
                ai_cctv_path=options.get('ai_cctv_path', ''),
                original_segment_seconds=options.get('original_segment_seconds', 10),
                clip_max_seconds=options.get('clip_max_seconds', 10))
        except Exception:
            self.worker = None
            self._set_status(camera, '오류')
            self.running_camera = None
            self.notice.emit('영상 작업을 준비하지 못했습니다. 입력과 저장 경로를 확인하세요.')
            return
        for signal, callback in ((self.worker.frame_ready, self._frame),
                                 (self.worker.metrics_ready, self.metrics_ready.emit),
                                 (self.worker.event_ready, self._event),
                                 (self.worker.loading_ready, self.loading_ready.emit),
                                 (self.worker.status_ready, self._status),
                                 (self.worker.finished, self._worker_finished)):
            signal.connect(lambda *args, cb=callback, gen=generation: self._dispatch_video(gen, cb, *args))
        self.started.emit(dict(camera))
        self._status('로딩 중')
        self.loading_ready.emit('시스템 시작 중…')
        self.worker.start()

    def _status(self, status):
        self._set_status(self.running_camera, status)

    def _frame(self, frame):
        self._status('LIVE')
        self.frame_ready.emit(frame)

    def _event(self, event):
        if event.get('type') == 'network_failure':
            self._status('연결 장애 / 재연결 중')
        self.event_ready.emit(event)

    def _worker_finished(self):
        self._terminal_error = True
        self.stop()

    def stop(self):
        if self.stopping:
            return
        self.stopping = True
        pending, self.detail_pending = self.detail_pending, None
        if self.detail_api is not None:
            self.detail_api.cancel()
        self.run_generation += 1
        camera = self.running_camera or (pending[1] if pending else None)
        if self.running_camera is None and camera is not None:
            self.running_camera = dict(camera)
        self._status('중지 중')
        self._stop_started = time.monotonic()
        self.changed.emit()
        self.stop_requested.emit()
        if self.worker is not None:
            self.worker.request_stop()
        self.notice.emit('영상 수신·녹화·분석을 정리하고 있습니다…')
        self._stop_timer.start()

    def begin_shutdown(self):
        self.shutting_down = True
        self.stop()

    def _poll_stop(self):
        if not self.stopping:
            return
        if (self.worker is not None and self.worker.isRunning()) or self.monitor_pending:
            if time.monotonic() - self._stop_started > 30:
                self.notice.emit('정리에 시간이 걸리고 있습니다. 분석·수신·복구 작업 종료를 기다리는 중입니다.')
            return
        if self.worker is not None:
            if not self.worker.cleanup_ok:
                self.notice.emit('작업 정리에 실패했습니다. 안전한 종료가 확인되지 않아 화면 전환을 중단했습니다.')
                self._stop_timer.stop()
                return
            self.worker.deleteLater()
            self.worker = None
        self._stop_timer.stop()
        self._status('오류' if self._terminal_error else '중지됨')
        self.running_camera = None
        self.stopping = False
        self.notice.emit('')
        self.changed.emit()
        self.stopped.emit()
