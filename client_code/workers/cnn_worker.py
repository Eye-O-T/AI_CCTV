"""로그인 세션의 모든 카메라가 공유하는 제한 큐와 단일 CNN 모델."""
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread


class CNNWorker(Thread):
    def __init__(self, analyzer_factory=None, capacity=32):
        super().__init__(name='CNNWorker', daemon=False)
        self._factory = analyzer_factory
        self._jobs = Queue(capacity)
        self._results = Queue(capacity * 2)
        self._lock = Lock()
        self._abort = Event()
        self._closing = False
        self.failed = False

    def submit(self, event_id, source_path):
        with self._lock:
            if self._closing or self._abort.is_set() or self.failed:
                return False
            try:
                self._jobs.put_nowait((event_id, source_path))
                return True
            except Full:
                return False

    def take_results(self):
        results = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except Empty:
                return results

    def finish(self):
        with self._lock:
            self._closing = True

    def abort(self):
        self.finish()
        self._abort.set()

    def run(self):
        analyzer = None
        try:
            try:
                factory = self._factory
                if factory is None:
                    # 모델 import/로딩은 GUI가 아닌 이 스레드에서만 수행한다.
                    if __package__ == 'workers':
                        from appearance.analyzer import AppearanceAnalyzer
                    else:
                        from ..appearance.analyzer import AppearanceAnalyzer
                    factory = AppearanceAnalyzer
                analyzer = factory()
            except Exception:
                self.failed = True
            while not self._abort.is_set():
                try:
                    event_id, source_path = self._jobs.get(timeout=.05)
                except Empty:
                    if self._closing or self.failed:
                        break
                    continue
                result, error = None, ''
                if self.failed:
                    error = 'CNN_INITIALIZATION_FAILED'
                else:
                    try:
                        result = analyzer.analyze(source_path)
                    except Exception:
                        error = 'CNN_INFERENCE_FAILED'
                while not self._abort.is_set():
                    try:
                        self._results.put(dict(event_id=event_id, result=result, error=error), timeout=.05)
                        break
                    except Full:
                        continue
        finally:
            self.failed = self.failed or analyzer is None
            # 취소된 세션의 경로/결과와 모델 참조를 남기지 않는다.
            analyzer = None
            while True:
                try:
                    self._jobs.get_nowait()
                except Empty:
                    break
