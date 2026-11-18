"""카메라 한 대의 전신 crop 후보를 제한 큐로 받아 평가·저장하는 스레드."""
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Full, Queue, SimpleQueue
from threading import Event, Lock, Thread
import time

import cv2

from detection.full_body_checker import FullBodyChecker
from storage.crop_collector import CropCollector


@dataclass
class SubmissionState:
    first_seen: datetime
    last_sample_at: float
    finished: bool = False


@dataclass
class CropTask:
    generation: int
    person_id: int
    bbox: tuple
    frame_shape: tuple
    captured_at: datetime
    first_seen: datetime
    image: object
    observation: SubmissionState


class CropWorker(Thread):
    def __init__(self, camera_id, output_root=None, queue_size=16, sample_interval=0.5):
        super().__init__(name=f'CropWorker-{camera_id}', daemon=False)
        self.collector = CropCollector(camera_id, output_root=output_root)
        self.full_body_checker = FullBodyChecker()
        self.tasks = Queue(maxsize=queue_size)
        self.results = SimpleQueue()
        self.sample_interval = sample_interval
        self._stopping = Event()
        self._lock = Lock()
        self._generation = 0
        self._observations = {}

    def submit(self, person_id, frame, bbox):
        """생산자에서 호출한다. 품질 검사·파일 저장·큐 공간 대기는 하지 않는다."""
        now = time.monotonic()
        captured_at = datetime.now()
        with self._lock:
            if self._stopping.is_set():
                return False
            state = self._observations.get(person_id)
            if state is not None:
                if state.finished or now - state.last_sample_at < self.sample_interval:
                    return False
                state.last_sample_at = now
            else:
                state = self._observations[person_id] = SubmissionState(captured_at, now)
            # 큐가 찼을 때에는 큰 이미지 복사도 생략한다. put_nowait도 Full을 처리한다.
            if self.tasks.full():
                return False
            height, width = frame.shape[:2]
            x1, y1, x2, y2 = map(int, bbox)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)
            if x2 <= x1 or y2 <= y1:
                return False
            task = CropTask(self._generation, person_id, tuple(bbox), tuple(frame.shape),
                            captured_at, state.first_seen, frame[y1:y2, x1:x2].copy(), state)
            try:
                self.tasks.put_nowait(task)
            except Full:
                return False
            return True

    def reset(self, generation):
        with self._lock:
            self._generation = generation
            self._observations.clear()
            self._discard_pending()

    def remove_person(self, person_id):
        with self._lock:
            self._observations.pop(person_id, None)
            self._discard_pending(person_id)

    def request_stop(self):
        with self._lock:
            self._stopping.set()
            self._observations.clear()
            self._discard_pending()

    def _discard_pending(self, person_id=None):
        retained = []
        while True:
            try:
                task = self.tasks.get_nowait()
            except Empty:
                break
            if person_id is not None and task.person_id != person_id:
                retained.append(task)
        for task in retained:
            self.tasks.put_nowait(task)

    def _current_observation(self, task):
        with self._lock:
            state = self._observations.get(task.person_id)
            if (self._stopping.is_set() or task.generation != self._generation
                    or state is not task.observation or state.finished):
                return None
            return state

    def take_results(self):
        results = []
        while True:
            try:
                results.append(self.results.get_nowait())
            except Empty:
                return results

    def run(self):
        generation = self._generation
        collecting = {}
        try:
            while not self._stopping.is_set():
                # Collector의 메모리는 이 스레드만 수정한다.
                with self._lock:
                    current_generation = self._generation
                    active = dict(self._observations)
                if generation != current_generation:
                    self.collector.clear()
                    collecting.clear()
                    generation = current_generation
                for person_id in list(self.collector.collections):
                    if collecting.get(person_id) is not active.get(person_id):
                        self.collector.remove_person(person_id)
                        collecting.pop(person_id, None)
                try:
                    task = self.tasks.get(timeout=0.1)
                except Empty:
                    continue
                state = self._current_observation(task)
                if state is None:
                    continue
                # reset 직후 새 세대 작업을 꺼낸 경우에도 이전 후보와 섞이지 않게 한다.
                if generation != task.generation:
                    self.collector.clear()
                    collecting.clear()
                    generation = task.generation
                if not self.full_body_checker.is_full_body_visible(task.bbox, task.frame_shape):
                    continue
                # 판정 중에도 STOP·추적 종료·reset이 들어올 수 있다.
                if self._current_observation(task) is None:
                    continue
                if collecting.get(task.person_id) is not state:
                    self.collector.remove_person(task.person_id)
                    collecting[task.person_id] = state
                error = None
                try:
                    paths = self.collector.collect_image(
                        task.person_id, task.image, task.first_seen,
                        lambda: self._current_observation(task) is not None)
                except (OSError, cv2.error):
                    paths = ()
                    error = '인물 crop 저장·품질 처리 실패. output 폴더 권한·디스크 공간을 확인하세요.'
                if paths or error:
                    with self._lock:
                        if (task.generation == self._generation
                                and self._observations.get(task.person_id) is state):
                            state.finished = True
                    self.results.put(dict(generation=task.generation, person_id=task.person_id,
                                          paths=paths, error=error))
        except Exception:
            self.results.put(dict(error='CropWorker 처리 오류로 crop 수집을 중단했습니다.', paths=()))
            self.request_stop()
        finally:
            self.collector.clear()
