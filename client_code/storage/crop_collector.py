"""한 카메라의 로컬 추적별로 품질 기준을 통과한 첫 5장을 저장한다."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import time
from uuid import uuid4

import cv2


@dataclass
class CropCollection:
    first_seen: datetime
    last_sample_at: float | None = None
    images: list = field(default_factory=list)
    saved_paths: tuple = ()
    failed: bool = False


class CropCollector:
    def __init__(self, camera_id, output_root=None, sample_interval=0.5,
                 min_width=64, min_height=128, min_sharpness=50.0):
        self.camera_id = camera_id
        self.output_root = (Path(output_root) if output_root is not None
                            else Path(__file__).resolve().parents[2] / 'output')
        self.sample_interval = sample_interval
        self.min_width = min_width
        self.min_height = min_height
        self.min_sharpness = min_sharpness
        self.collections = {}

    def collect(self, person_id, frame, bbox):
        """저장 직후에만 경로 5개를 반환한다. 완료·실패 상태는 추적 종료까지 유지한다."""
        state = self.collections.get(person_id)
        if state is None:
            state = self.collections[person_id] = CropCollection(datetime.now())
        if state.saved_paths or state.failed:
            return ()

        now = time.monotonic()
        if state.last_sample_at is not None and now - state.last_sample_at < self.sample_interval:
            return ()
        # 탈락한 후보도 간격에 포함해 매 프레임 품질을 계산하지 않는다.
        state.last_sample_at = now
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = map(int, bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        if x2 - x1 < self.min_width or y2 - y1 < self.min_height:
            return ()
        image = frame[y1:y2, x1:x2]
        return self.collect_image(person_id, image, first_seen=state.first_seen)

    def collect_image(self, person_id, image, first_seen=None, is_active=None):
        """비동기 소비자용: 생산자가 이미 간격을 제한하고 복사한 crop을 평가한다."""
        state = self.collections.get(person_id)
        if state is None:
            state = self.collections[person_id] = CropCollection(first_seen or datetime.now())
        if state.saved_paths or state.failed:
            return ()
        height, width = image.shape[:2]
        if width < self.min_width or height < self.min_height:
            return ()
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if cv2.Laplacian(gray, cv2.CV_64F).var() < self.min_sharpness:
            return ()
        # 품질 계산 도중 끝난 관측은 후보 추가·새 저장을 시작하지 않는다.
        if is_active is not None and not is_active():
            return ()
        # 원본 프레임 변경과 메모리 수명에서 후보를 분리한다.
        state.images.append(image.copy())
        if len(state.images) < 5:
            return ()

        try:
            state.saved_paths = self._save(person_id, state)
            return state.saved_paths
        except (OSError, cv2.error):
            # 자동 재시도 없이 호출자에게 한 번만 저장 실패를 알린다.
            state.failed = True
            raise
        finally:
            state.images.clear()

    def _save(self, person_id, state):
        day = self.output_root / f'camera_{self.camera_id}' / state.first_seen.strftime('%Y-%m-%d')
        day.mkdir(parents=True, exist_ok=True)
        name = f"person_{person_id}_{state.first_seen.strftime('%H%M%S%f')[:-3]}"
        staging = day / f'.{name}_{uuid4().hex[:8]}.pending'
        staging.mkdir()
        written = []
        try:
            for index, image in enumerate(state.images, 1):
                ok, encoded = cv2.imencode('.jpg', image)
                if not ok:
                    raise OSError('Crop JPEG encoding failed')
                path = staging / f'crop_{index:02d}.jpg'
                written.append(path)
                # 한글을 포함하는 Windows 경로도 지원한다.
                path.write_bytes(encoded.tobytes())
            destination = day / name
            while True:
                try:
                    staging.rename(destination)
                    break
                except FileExistsError:
                    destination = day / f'{name}_{uuid4().hex[:8]}'
            return tuple(destination / path.name for path in written)
        except (OSError, cv2.error):
            # 이번 저장에서 생성한 파일만 정리한다. 기존 저장물은 건드리지 않는다.
            for path in written:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                staging.rmdir()
            except OSError:
                pass
            raise

    def remove_person(self, person_id):
        self.collections.pop(person_id, None)

    def clear(self):
        self.collections.clear()
