# clip_manager.py

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import hypot
from pathlib import Path
from uuid import uuid4

import cv2


@dataclass(frozen=True)
class PersonMediaUpdate:
    """완료된 파일만 노출하는 관측별 스냅샷. 영상 프레임은 보관하지 않는다."""
    camera_id: int
    execution_id: str
    tracking_generation: int
    local_person_id: int
    folder_path: Path
    first_seen: datetime
    last_seen: datetime
    finished_at: datetime | None
    clip_path: Path | None
    crop_path: Path | None
    trajectory_path: Path | None
    crop_task_id: object = None
    global_person_id: int | None = None
    move_failed: bool = False
    first_seen_utc: datetime | None = None
    last_seen_utc: datetime | None = None
    finish_reason: str | None = None
    revision: int = 0
    cnn_source_path: Path | None = None


class ClipManager:
    def __init__(
        self,
        base_dir,
        fps=30,
        max_clip_seconds=10,
        disappear_timeout=3.0,
        trajectory_sample_interval=5,
        trajectory_min_distance=10,
        trajectory_smoothing_alpha=0.35,
        trajectory_simplify_epsilon=4.0,
        target_bitrate_kbps=2500,
        camera_id=1,
        execution_id=None,
        on_update=None,
        destination_root=None,
        on_error=None,
    ):
        self.base_dir = base_dir
        self.camera_id = camera_id
        self.execution_id = execution_id or uuid4().hex
        self.on_update = on_update
        self.destination_root = Path(destination_root).resolve() if destination_root else None
        self.on_error = on_error
        self.storage_failed = False
        self.fps = fps if fps and fps > 0 else 30
        self.frame_interval = timedelta(seconds=1.0 / self.fps)
        self.max_clip_seconds = max_clip_seconds
        self.target_bitrate_kbps = target_bitrate_kbps
        self.disappear_timeout = disappear_timeout
        self.trajectory_sample_interval = max(1, int(trajectory_sample_interval))
        self.trajectory_min_distance = max(0, int(trajectory_min_distance))
        self.trajectory_smoothing_alpha = min(
            1.0,
            max(0.0, float(trajectory_smoothing_alpha)),
        )
        self.trajectory_simplify_epsilon = max(0.0, float(trajectory_simplify_epsilon))
        now = datetime.now()
        parent = Path(base_dir) / now.strftime('%Y-%m-%d') / f'camera_{camera_id}'
        parent.mkdir(parents=True, exist_ok=True)
        self.clip_root_dir = str(self._make_directory(parent, now.strftime('실행_%H시%M분%S초_%f')))
        self.person_clips = {}

    def update_person(self, person_id, frame, bbox, generation=0):
        if frame is None or bbox is None:
            return

        state = self.person_clips.get(person_id)
        if state is not None and state['generation'] != generation:
            self.finish_person(person_id)
            state = None
        if state is None:
            state = self._create_person_state(person_id, frame, generation)
            self.person_clips[person_id] = state

        state["last_seen"] = datetime.now()
        state['last_seen_utc'] = state['last_seen'].astimezone(timezone.utc)
        state["last_frame"] = frame.copy()
        self._add_trajectory_point(state, bbox)

        if state["clip_completed"]:
            return

        frame_size = self._get_frame_size(frame)
        if state["writer"] is None:
            self._start_new_clip(state, frame_size)

        if state["writer"] is None:
            return

        now = datetime.now()
        write_until = self._get_preview_write_until(state, now)
        self._write_frame_by_wall_clock(state, frame, write_until)

        if self._should_complete_preview(state, now):
            self._close_writer(state)

    def finish_person(self, person_id, reason='cleanup'):
        state = self.person_clips.get(person_id)
        if state is None:
            return

        self._close_writer(state)
        self._save_trajectory_image(state)
        state['finished_at'] = state['last_seen']
        state['finish_reason'] = reason
        self._notify(state)
        del self.person_clips[person_id]

    def finish_all(self, reason='cleanup'):
        errors = []
        for person_id in list(self.person_clips.keys()):
            try:
                self.finish_person(person_id, reason=reason)
            except Exception as error:
                errors.append(error)
        if errors:
            raise OSError("인물 클립 또는 궤적 마무리 실패") from errors[0]
        if self.destination_root is not None:
            try:
                Path(self.clip_root_dir).rmdir()
            except OSError:
                pass  # 미확정/실패 자료 및 다른 작업은 보존한다.

    def _create_person_state(self, person_id, frame, generation):
        now = datetime.now()
        folder_name = now.strftime('인물_%H시%M분%S초_%f') + f'_local_{person_id}'
        Path(self.clip_root_dir).mkdir(parents=True, exist_ok=True)
        folder_path = str(self._make_directory(Path(self.clip_root_dir), folder_name))

        return {
            "person_id": person_id,
            "generation": generation,
            "first_seen": now,
            "last_seen": now,
            'first_seen_utc': now.astimezone(timezone.utc),
            'last_seen_utc': now.astimezone(timezone.utc),
            'finish_reason': None,
            'revision': 0,
            "folder_path": folder_path,
            "clip_index": 0,
            "clip_started_at": None,
            "next_frame_time": None,
            "frames_written": 0,
            "clip_path": None,
            "clip_completed": False,
            "writer": None,
            "points": [],
            "trajectory_frame_count": 0,
            "last_trajectory_point": None,
            "last_bbox_diagonal": None,
            "jump_skip_count": 0,
            "last_frame": frame.copy(),
            "crop_saved": False,
            'cnn_source_path': None,
            "crop_task_id": None,
            "trajectory_path": None,
            "finished_at": None,
            "global_person_id": None,
            "destination_directory": None,
            "moved_files": {},
            "failed_moves": set(),
            "directory_failed": False,
            "directory_finalized": False,
            "move_failed": False,
        }

    def _start_new_clip(self, state, frame_size):
        state["clip_index"] = 1
        state["clip_started_at"] = datetime.now()
        state["next_frame_time"] = state["clip_started_at"]
        state["frames_written"] = 0

        clip_filename = "clip.mp4"
        clip_path = os.path.join(state["folder_path"], clip_filename)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(clip_path, fourcc, self.fps, frame_size)
        self._apply_writer_profile(writer)

        if not writer.isOpened():
            writer.release()
            state["writer"] = None
            state['clip_path'] = None
            raise OSError("클립 영상 Writer 생성 실패")

        state["writer"] = writer
        state["clip_path"] = clip_path

        print(
            f"클립 영상 저장 시작: {clip_path} "
            f"({frame_size[0]}x{frame_size[1]}, {self.fps:.1f}fps, "
            f"목표 {self.target_bitrate_kbps}kbps급)"
        )

    def _apply_writer_profile(self, writer):
        if writer is None:
            return

        if hasattr(cv2, "VIDEOWRITER_PROP_QUALITY"):
            writer.set(cv2.VIDEOWRITER_PROP_QUALITY, 75)

    def _close_writer(self, state):
        writer = state.get("writer")
        if writer is not None:
            ended_at = datetime.now()
            wall_seconds = (
                ended_at - state["clip_started_at"]
            ).total_seconds()
            encoded_seconds = state["frames_written"] / self.fps if self.fps else 0
            effective_fps = (
                state["frames_written"] / wall_seconds
                if wall_seconds > 0
                else 0
            )
            writer.release()
            state["writer"] = None
            state['clip_completed'] = True
            print(
                "클립 영상 저장 종료: "
                f"{state.get('clip_path')} "
                f"(실시간 {wall_seconds:.2f}s, 재생예상 {encoded_seconds:.2f}s, "
                f"저장프레임 {state['frames_written']}, 실효FPS {effective_fps:.2f})"
            )
            state["next_frame_time"] = None
            state["frames_written"] = 0
            self._notify(state)

    def _write_frame_by_wall_clock(self, state, frame, now):
        writer = state.get("writer")
        next_frame_time = state.get("next_frame_time")

        if writer is None or next_frame_time is None:
            return

        writes = 0
        max_writes_per_input = max(1, int(self.fps * 2))

        while state["next_frame_time"] <= now and writes < max_writes_per_input:
            writer.write(frame)
            state["next_frame_time"] += self.frame_interval
            state["frames_written"] += 1
            writes += 1

        if writes == max_writes_per_input and state["next_frame_time"] <= now:
            state["next_frame_time"] = now + self.frame_interval
            print("클립 영상 프레임 보정 한도 초과: 긴 지연 구간을 건너뜁니다.")

    def _get_preview_write_until(self, state, now):
        if self.max_clip_seconds is None:
            return now

        clip_deadline = state["clip_started_at"] + timedelta(
            seconds=self.max_clip_seconds
        )
        return min(now, clip_deadline)

    def _should_complete_preview(self, state, now):
        if self.max_clip_seconds is None:
            return False

        if state["clip_started_at"] is None:
            return False

        elapsed_seconds = (now - state["clip_started_at"]).total_seconds()
        return elapsed_seconds >= self.max_clip_seconds

    def _save_trajectory_image(self, state):
        frame = state.get("last_frame")
        points = state.get("points", [])

        if frame is None or len(points) == 0:
            return

        trajectory_frame = frame.copy()
        points = self._simplify_points(points)

        for index, point in enumerate(points):
            cv2.circle(trajectory_frame, point, 4, (0, 255, 255), -1)

            if index > 0:
                cv2.line(
                    trajectory_frame,
                    points[index - 1],
                    point,
                    (0, 255, 255),
                    2,
                )

        save_path = os.path.join(state["folder_path"], "trajectory.jpg")
        # imencode + tofile also supports Windows paths containing Korean text.
        encoded, image = cv2.imencode(".jpg", trajectory_frame)
        if not encoded:
            raise OSError("궤적 이미지 인코딩 실패")
        image.tofile(save_path)
        state['trajectory_path'] = save_path

    def _add_trajectory_point(self, state, bbox):
        state["trajectory_frame_count"] += 1

        raw_point = self._get_bbox_ground_anchor(bbox)
        bbox_diagonal = self._get_bbox_diagonal(bbox)
        previous_point = state.get("last_trajectory_point")

        if previous_point is None:
            self._append_trajectory_point(state, raw_point, bbox_diagonal)
            return

        if state["trajectory_frame_count"] % self.trajectory_sample_interval != 0:
            return

        distance = self._distance(previous_point, raw_point)
        if distance < self.trajectory_min_distance:
            return

        max_jump_distance = max(80.0, bbox_diagonal * 1.4)
        if distance > max_jump_distance:
            state["jump_skip_count"] += 1
            if state["jump_skip_count"] < 3:
                return

            state["last_trajectory_point"] = (
                float(raw_point[0]),
                float(raw_point[1]),
            )
            state["last_bbox_diagonal"] = bbox_diagonal
            state["jump_skip_count"] = 0
            return

        state["jump_skip_count"] = 0
        alpha = self.trajectory_smoothing_alpha
        smoothed_point = (
            previous_point[0] * (1.0 - alpha) + raw_point[0] * alpha,
            previous_point[1] * (1.0 - alpha) + raw_point[1] * alpha,
        )
        self._append_trajectory_point(state, smoothed_point, bbox_diagonal)

    def _append_trajectory_point(self, state, point, bbox_diagonal):
        float_point = (float(point[0]), float(point[1]))
        int_point = (int(round(float_point[0])), int(round(float_point[1])))

        points = state["points"]
        if points and points[-1] == int_point:
            state["last_trajectory_point"] = float_point
            state["last_bbox_diagonal"] = bbox_diagonal
            return

        points.append(int_point)
        state["last_trajectory_point"] = float_point
        state["last_bbox_diagonal"] = bbox_diagonal

    def _simplify_points(self, points):
        if len(points) <= 2 or self.trajectory_simplify_epsilon <= 0:
            return points

        simplified = cv2.approxPolyDP(
            curve=self._points_to_curve(points),
            epsilon=self.trajectory_simplify_epsilon,
            closed=False,
        )
        return [tuple(point[0]) for point in simplified]

    def _points_to_curve(self, points):
        import numpy as np

        return np.array(points, dtype="int32").reshape((-1, 1, 2))

    def save_representative_crop(self, person_id, crop_path, generation, task_id):
        """VideoWorker가 현재 관측임을 확인한 OSNet 첫 crop만 전달한다."""
        state = self.person_clips.get(person_id)
        if state is None or state['generation'] != generation or state['crop_saved']:
            return False
        folder = Path(state['folder_path'])
        # 대표 이미지 복제가 실패해도 같은 관측의 유효한 글로벌 매칭은 연결할 수 있다.
        state['crop_task_id'] = task_id
        pending = folder / 'crop.pending'
        # OSNet 원본 5장은 output에 보존한다. 복제 완료 전에는 ready 경로를 알리지 않는다.
        shutil.copyfile(crop_path, pending)
        destination = folder / 'crop.jpg'
        os.rename(pending, destination)
        state['crop_saved'] = True
        state['cnn_source_path'] = Path(crop_path).resolve()
        self._notify(state)
        return True

    def assign_global_person(self, observation, global_person_id):
        """현재 Worker가 검증한 매칭 결과를 원래 관측에 한 번 연결한다."""
        state = self.person_clips.get(observation.local_person_id)
        if (state is None or observation.camera_id != self.camera_id
                or observation.execution_id != self.execution_id
                or observation.tracking_generation != state['generation']
                or observation.task_id != state['crop_task_id']
                or type(global_person_id) is not int or global_person_id < 1):
            return False
        if state['global_person_id'] not in (None, global_person_id):
            return False
        state['global_person_id'] = global_person_id
        self._notify(state)
        return True

    def _move_error(self, state):
        self.storage_failed = True
        if not state['move_failed']:
            state['move_failed'] = True
            if self.on_error is not None:
                self.on_error('인물 자료 이동·폴더명 확정 실패. temp 원본과 사용자 저장 경로를 확인하세요.')

    def _publish_ready_files(self, state):
        if self.destination_root is None or state['global_person_id'] is None:
            return
        sources = {
            'clip.mp4': Path(state['clip_path']) if state['clip_completed'] else None,
            'crop.jpg': Path(state['folder_path']) / 'crop.jpg' if state['crop_saved'] else None,
            'trajectory.jpg': Path(state['trajectory_path']) if state['trajectory_path'] else None,
        }
        if not any(sources.values()) or state['directory_failed']:
            return
        if state['destination_directory'] is None:
            try:
                parent = (self.destination_root / '이벤트' / f"person_{state['global_person_id']}"
                          / state['first_seen'].strftime('%Y-%m-%d') / f'camera_{self.camera_id}')
                parent.mkdir(parents=True, exist_ok=True)
                state['destination_directory'] = self._make_directory(
                    parent, state['first_seen'].strftime('%H시%M분%S초'))
            except OSError:
                state['directory_failed'] = True
                self._move_error(state)
                return

        for name, source in sources.items():
            if source is None or name in state['moved_files'] or name in state['failed_moves']:
                continue
            target = state['destination_directory'] / name
            try:
                # Windows rename은 덮어쓰기/볼륨 간 복사를 하지 않는다.
                if target.exists():
                    raise FileExistsError('Destination already exists')
                os.rename(source, target)
                state['moved_files'][name] = target
            except OSError:
                state['failed_moves'].add(name)
                self._move_error(state)

        if state['finished_at'] is not None and not state['directory_finalized']:
            old = state['destination_directory']
            ended = state['finished_at']
            end_label = ended.strftime('%H시%M분%S초')
            if ended.date() != state['first_seen'].date():
                end_label = ended.strftime('%Y-%m-%d_%H시%M분%S초')
            label = state['first_seen'].strftime('%H시%M분%S초') + '~' + end_label
            sequence = 1
            try:
                while True:
                    new = old.parent / (label if sequence == 1 else f'{label}_{sequence}')
                    if new.exists():
                        sequence += 1
                        continue
                    try:
                        os.rename(old, new)
                        break
                    except FileExistsError:
                        sequence += 1
                state['destination_directory'] = new
                state['moved_files'] = {name: new / name for name in state['moved_files']}
            except OSError:
                self._move_error(state)
            # 파일 이동 실패와 동일하게 자동 재시도하지 않는다.
            state['directory_finalized'] = True

        if state['finished_at'] is not None and not state['move_failed']:
            try:
                Path(state['folder_path']).rmdir()
            except OSError:
                pass  # 이번 관측의 빈 폴더만 제거한다. 부분 파일이 있으면 보존한다.

    def _notify(self, state):
        self._publish_ready_files(state)
        state['revision'] += 1
        if self.on_update is not None:
            self.on_update(PersonMediaUpdate(
                self.camera_id, self.execution_id, state['generation'], state['person_id'],
                state['destination_directory'] or Path(state['folder_path']),
                state['first_seen'], state['last_seen'], state['finished_at'],
                state['moved_files'].get('clip.mp4', Path(state['clip_path']) if state['clip_completed'] else None),
                state['moved_files'].get('crop.jpg', Path(state['folder_path']) / 'crop.jpg' if state['crop_saved'] else None),
                state['moved_files'].get('trajectory.jpg', Path(state['trajectory_path']) if state['trajectory_path'] else None),
                state['crop_task_id'],
                state['global_person_id'], state['move_failed'],
                state['first_seen_utc'], state['last_seen_utc'], state['finish_reason'], state['revision'],
                state['cnn_source_path'],
            ))

    def _get_bbox_ground_anchor(self, bbox):
        x1, y1, x2, y2 = map(int, bbox)
        return ((x1 + x2) // 2, y2)

    def _get_bbox_diagonal(self, bbox):
        x1, y1, x2, y2 = map(int, bbox)
        return hypot(x2 - x1, y2 - y1)

    def _distance(self, point_a, point_b):
        return hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])

    def _get_frame_size(self, frame):
        height, width = frame.shape[:2]
        return width, height

    @staticmethod
    def _make_directory(parent, folder_name):
        index = 1
        while True:
            candidate = parent / (folder_name if index == 1 else f'{folder_name}_{index}')
            try:
                candidate.mkdir()
                return candidate
            except FileExistsError:
                index += 1
