"""카메라별 제한 큐. 공유 모델 추론 후 공용 Manager에 한 번만 제출한다."""
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread

if __package__ == 'workers':
    from reid.tasks import MatchTask, Observation
else:
    from ..reid.tasks import MatchTask, Observation


@dataclass(frozen=True)
class CropJob:
    observation: Observation
    paths: tuple


class OSNetWorker(Thread):
    def __init__(self, pipeline, camera_id, execution_id, queue_size=16):
        super().__init__(name=f'OSNetWorker-{camera_id}', daemon=False)
        self.pipeline = pipeline
        self.camera_id = camera_id
        self.execution_id = execution_id
        self._queue = Queue(maxsize=queue_size)
        self._results = Queue(maxsize=32)
        self._lock = RLock()
        self._stop_event = Event()
        self._active = {}
        self._manager = None
        self.failed = False

    def submit(self, observation, paths):
        with self._lock:
            if (self._stop_event.is_set() or self.failed or observation.camera_id != self.camera_id
                    or observation.execution_id != self.execution_id or observation.task_id in self._active):
                return False
            try:
                self._queue.put_nowait(CropJob(observation, tuple(paths)))
            except Full:
                return False
            self._active[observation.task_id] = observation
            return True

    def _end(self, task_id):
        self._active.pop(task_id, None)
        if self._manager is not None:
            self._manager.end_observation(task_id)

    @property
    def accepting(self):
        return self.is_alive() and not self._stop_event.is_set() and not self.failed

    def has_observation(self, task_id):
        with self._lock:
            return task_id in self._active

    def end_person(self, person_id):
        with self._lock:
            for task_id, observation in list(self._active.items()):
                if observation.local_person_id == person_id:
                    self._end(task_id)

    def reset(self):
        with self._lock:
            for task_id in list(self._active):
                self._end(task_id)
            while True:
                try:
                    self._queue.get_nowait()
                except Empty:
                    break

    def request_stop(self):
        self._stop_event.set()
        self.reset()

    def _current(self, observation):
        return not self._stop_event.is_set() and self._active.get(observation.task_id) == observation

    def deliver(self, result):
        with self._lock:
            if not self._current(result.observation):
                return
            try:
                self._results.put_nowait(result)
            except Full:
                self.pipeline.notice(self.camera_id, 'RESULT_QUEUE_FULL')

    def take_results(self):
        results = []
        while True:
            try:
                result = self._results.get_nowait()
            except Empty:
                return results
            with self._lock:
                if self._current(result.observation):
                    results.append(result)

    def run(self):
        try:
            while not self._stop_event.is_set():
                try:
                    job = self._queue.get(timeout=.1)
                except Empty:
                    continue
                components = None
                while not self._stop_event.is_set():
                    with self._lock:
                        if not self._current(job.observation):
                            break
                    components = self.pipeline.components()
                    if components is not None or self.pipeline.failed:
                        break
                    self._stop_event.wait(.05)
                with self._lock:
                    if not self._current(job.observation):
                        continue
                if components is None:
                    if self.pipeline.failed:
                        break
                    continue
                extractor, manager = components
                self._manager = manager
                try:
                    vector = extractor.extract(job.paths)
                    task = MatchTask(job.observation, vector)
                except Exception as error:
                    self.pipeline.notice(self.camera_id, getattr(error, 'code', 'INFERENCE_FAILED'))
                    with self._lock:
                        self._end(job.observation.task_id)
                    continue
                # 변환 중 STOP·추적 종료가 들어오면 Manager에 다시 등록하지 않는다.
                while not self._stop_event.is_set():
                    with self._lock:
                        if not self._current(job.observation):
                            break
                        manager.begin_observation(job.observation)
                        if manager.submit(task):
                            break
                    if manager.state == 'STOPPED':
                        break
                    self._stop_event.wait(.05)
        except Exception:
            self.failed = True
            self.pipeline.notice(self.camera_id, 'OSNET_WORKER_FAILED')
        finally:
            self._stop_event.set()
            self.reset()
