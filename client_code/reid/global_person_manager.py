"""공용 큐 하나로 매칭 결정·저장 확인·메모리 반영을 순차 처리한다."""
from dataclasses import dataclass
import math
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread

from .api_client import GlobalPersonApiError
from .config import SIMILARITY_THRESHOLD
from .tasks import MatchResult, MatchTask, Observation


@dataclass(frozen=True)
class _Decision:
    task: MatchTask
    mode: str
    person_id: int | None
    similarity: float | None


class GlobalPersonManager(Thread):
    def __init__(self, api, threshold=SIMILARITY_THRESHOLD, queue_size=32):
        if not math.isfinite(threshold) or not -1 <= threshold <= 1 or queue_size < 1:
            raise ValueError('유효한 임계값과 큐 크기가 필요합니다.')
        super().__init__(name='GlobalPersonManager', daemon=False)
        self.api = api
        self.threshold = threshold
        self._queue = Queue(maxsize=queue_size)
        self._results = Queue(maxsize=queue_size)
        self._lock = RLock()
        self._stop_event = Event()
        self._retry_event = Event()
        self._ready_event = Event()
        self._state = 'STARTING'
        self._error_code = ''
        self._retryable = False
        self._candidates = {}
        self._active = {}
        self._bindings = {}
        self._submitted = set()

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def error_code(self):
        with self._lock:
            return self._error_code

    def wait_until_ready(self, timeout=None):
        self._ready_event.wait(timeout)
        return self.state == 'READY'

    def begin_observation(self, observation):
        if not isinstance(observation, Observation):
            raise ValueError('관측 정보가 필요합니다.')
        with self._lock:
            if self._stop_event.is_set() or self._state == 'STOPPED':
                return False
            previous = self._active.get(observation.task_id)
            if previous is not None:
                if previous != observation:
                    raise ValueError('동일 작업 ID의 관측 정보가 다릅니다.')
                return True
            for task_id, old in list(self._active.items()):
                if old.local_key == observation.local_key:
                    self._end(task_id)
            self._active[observation.task_id] = observation
            return True

    def _end(self, task_id):
        self._active.pop(task_id, None)
        self._bindings.pop(task_id, None)
        self._submitted.discard(task_id)

    def end_observation(self, task_id):
        with self._lock:
            self._end(task_id)

    def end_camera(self, camera_id, execution_id=None):
        with self._lock:
            for task_id, observation in list(self._active.items()):
                if observation.camera_id == camera_id and (execution_id is None or observation.execution_id == execution_id):
                    self._end(task_id)

    def submit(self, task):
        if not isinstance(task, MatchTask):
            raise ValueError('매칭 작업이 필요합니다.')
        with self._lock:
            task_id = task.observation.task_id
            if (self._state != 'READY' or self._stop_event.is_set()
                    or self._active.get(task_id) != task.observation or task_id in self._submitted):
                return False
            try:
                self._queue.put_nowait(task)
            except Full:
                return False
            self._submitted.add(task_id)
            return True

    def take_results(self):
        results = []
        while True:
            try:
                result = self._results.get_nowait()
            except Empty:
                return results
            with self._lock:
                if self._active.get(result.observation.task_id) == result.observation:
                    results.append(result)

    def retry_pending(self):
        with self._lock:
            if self._state != 'ERROR' or not self._retryable or self._stop_event.is_set():
                return False
            self._retry_event.set()
            return True

    def request_stop(self):
        self._stop_event.set()
        self._retry_event.set()
        with self._lock:
            if self._state == 'STOPPED':
                return
            self._state = 'STOPPING'
            self._active.clear()
            self._bindings.clear()

    def _pause(self, error):
        self._retry_event.clear()
        with self._lock:
            self._state = 'ERROR'
            self._error_code = error.code
            self._retryable = error.retryable
            self._ready_event.clear()
        while not self._stop_event.is_set():
            if self._retry_event.wait(.1):
                self._retry_event.clear()
                return not self._stop_event.is_set()
        return False

    def _ready(self):
        with self._lock:
            if self._stop_event.is_set():
                return
            self._state = 'READY'
            self._error_code = ''
            self._retryable = False
            self._ready_event.set()

    def _decide(self, task):
        with self._lock:
            observation = task.observation
            if self._active.get(observation.task_id) != observation:
                return None
            occupied = {person_id for task_id, person_id in self._bindings.items()
                        if task_id != observation.task_id and self._active[task_id].camera_id == observation.camera_id}
            best_id, best_score = None, -math.inf
            a = task.feature_vector
            a_norm = math.sqrt(math.fsum(value * value for value in a))
            for person_id, record in sorted(self._candidates.items()):
                if person_id in occupied:
                    continue
                b = record.feature_vector
                score = math.fsum(x * y for x, y in zip(a, b)) / (
                    a_norm * math.sqrt(math.fsum(value * value for value in b)))
                score = max(-1.0, min(1.0, score))
                if score > best_score:
                    best_id, best_score = person_id, score
            if best_id is not None and best_score >= self.threshold:
                return _Decision(task, 'existing', best_id, best_score)
            return _Decision(task, 'new', None, best_score if best_id is not None else None)

    def _save(self, decision):
        task = decision.task
        if decision.mode == 'new':
            record = self.api.create_person(task.observation.task_id, task.feature_vector, task.observation.observed_at)
            expected_vector = task.feature_vector
            if record.global_person_id in self._candidates:
                raise GlobalPersonApiError('INVALID_RESPONSE', retryable=True)
        else:
            record = self.api.update_last_seen(decision.person_id, task.observation.observed_at)
            expected_vector = self._candidates[decision.person_id].feature_vector
        if (record.feature_vector != expected_vector or record.last_seen_at < task.observation.observed_at
                or (decision.mode == 'existing' and record.global_person_id != decision.person_id)):
            raise GlobalPersonApiError('INVALID_RESPONSE', retryable=True)
        return record

    def run(self):
        initialized = False
        pending = None
        try:
            while not self._stop_event.is_set():
                if not initialized:
                    try:
                        records = self.api.list_people()
                        with self._lock:
                            self._candidates = {record.global_person_id: record for record in records}
                        initialized = True
                        self._ready()
                    except GlobalPersonApiError as error:
                        if not self._pause(error):
                            break
                        continue
                if pending is None:
                    try:
                        task = self._queue.get(timeout=.1)
                    except Empty:
                        continue
                    pending = self._decide(task)
                    if pending is None:
                        continue
                try:
                    if self._stop_event.is_set():
                        break
                    record = self._save(pending)
                except GlobalPersonApiError as error:
                    # 같은 결정을 보존한다. 불확실한 저장을 신규 판단으로 다시 실행하지 않는다.
                    if not self._pause(error):
                        break
                    continue
                with self._lock:
                    self._candidates[record.global_person_id] = record
                    observation = pending.task.observation
                    current = self._active.get(observation.task_id) == observation
                    if current and not self._stop_event.is_set():
                        self._bindings[observation.task_id] = record.global_person_id
                if current:
                    result = MatchResult(observation, record.global_person_id, pending.mode, pending.similarity)
                    while not self._stop_event.is_set():
                        try:
                            self._results.put(result, timeout=.1)
                            break
                        except Full:
                            continue
                pending = None
                self._ready()
        except Exception:
            with self._lock:
                self._error_code = 'MANAGER_ERROR'
        finally:
            try:
                self.api.close()
            finally:
                with self._lock:
                    self._state = 'STOPPED'
                    self._ready_event.set()
                    self._active.clear()
                    self._bindings.clear()
                    self._candidates.clear()
                    self._submitted.clear()
                    for queue in (self._queue, self._results):
                        while True:
                            try:
                                queue.get_nowait()
                            except Empty:
                                break
