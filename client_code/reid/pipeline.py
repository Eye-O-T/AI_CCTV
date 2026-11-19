"""로그인 실행당 공유 모델/Manager 한 개. Qt 객체는 작업 스레드에서 접근하지 않는다."""
from threading import Event, RLock, Thread

from .api_client import GlobalPersonApiClient
from .global_person_manager import GlobalPersonManager


class ReIDPipeline(Thread):
    def __init__(self, base_url, access_token, extractor_factory=None, api_factory=GlobalPersonApiClient):
        super().__init__(name='ReIDPipeline', daemon=False)
        self.base_url = base_url
        self._token = access_token
        self._extractor_factory = extractor_factory
        self._api_factory = api_factory
        self._lock = RLock()
        self._stop_event = Event()
        self._started_once = False
        self._workers = {}
        self._notices = set()
        self._extractor = self._manager = self._api = None
        self.failed = False

    def attach(self, camera_id, execution_id):
        if __package__ == 'reid':
            from workers.osnet_worker import OSNetWorker
        else:
            from ..workers.osnet_worker import OSNetWorker
        with self._lock:
            if self._stop_event.is_set() or self.failed:
                raise RuntimeError('ReID 파이프라인 종료 중입니다.')
            key = (camera_id, execution_id)
            if key in self._workers:
                raise ValueError('이미 등록된 카메라 실행입니다.')
            worker = OSNetWorker(self, camera_id, execution_id)
            self._workers[key] = worker
            if not self._started_once:
                self._started_once = True
                self.start()
            worker.start()
            return worker

    def detach(self, worker):
        worker.request_stop()
        with self._lock:
            self._workers.pop((worker.camera_id, worker.execution_id), None)

    def components(self):
        with self._lock:
            if self._manager is not None and self._manager.state == 'READY' and not self._stop_event.is_set():
                return self._extractor, self._manager
        return None

    def notice(self, camera_id, code):
        with self._lock:
            self._notices.add((camera_id, code))

    def take_notices(self):
        with self._lock:
            notices = list(self._notices)
            self._notices.clear()
            return notices

    def set_access_token(self, token):
        with self._lock:
            if not self._stop_event.is_set():
                self._token = token
                if self._api is not None:
                    self._api.set_access_token(token)

    def retry_pending(self):
        with self._lock:
            return self._manager is not None and self._manager.retry_pending()

    def request_stop(self):
        self._stop_event.set()
        with self._lock:
            self._token = None
            workers = list(self._workers.values())
            manager = self._manager
        for worker in workers:
            worker.request_stop()
        if manager is not None:
            manager.request_stop()

    def run(self):
        manager = None
        try:
            factory = self._extractor_factory
            if factory is None:
                from .osnet_extractor import OSNetExtractor
                factory = OSNetExtractor
            extractor = factory()
            if self._stop_event.is_set() or self.failed:
                return
            with self._lock:
                self._extractor = extractor
                self._api = self._api_factory(self.base_url, self._token)
                manager = self._manager = GlobalPersonManager(self._api)
                manager.start()
            last_error = None
            while not self._stop_event.is_set():
                if manager.state in ('ERROR', 'STOPPED'):
                    code = manager.error_code or 'MANAGER_STOPPED'
                    if last_error != code:
                        self.notice(None, code)
                        last_error = code
                    if manager.state == 'STOPPED':
                        self.failed = True
                        break
                else:
                    last_error = None
                for result in manager.take_results():
                    with self._lock:
                        worker = self._workers.get((result.observation.camera_id, result.observation.execution_id))
                    if worker is not None:
                        worker.deliver(result)
                self._stop_event.wait(.05)
        except Exception as error:
            self.failed = True
            self.notice(None, getattr(error, 'code', 'REID_INITIALIZATION_FAILED'))
        finally:
            with self._lock:
                workers = list(self._workers.values())
                self._token = None
            for worker in workers:
                worker.request_stop()
            for worker in workers:
                worker.join()
            if manager is not None:
                manager.request_stop()
                manager.join()
            elif self._api is not None:
                self._api.close()
            with self._lock:
                self._extractor = self._manager = self._api = None
                self._workers.clear()
                self._workers.clear()
