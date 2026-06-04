# clip_manager.py

import os
import shutil
from datetime import datetime, timedelta

import cv2


class ClipManager:
    def __init__(
        self,
        base_dir,
        fps=30,
        max_clip_seconds=10,
        disappear_timeout=3.0,
    ):
        self.base_dir = base_dir
        self.fps = fps if fps and fps > 0 else 30
        self.frame_interval = timedelta(seconds=1.0 / self.fps)
        self.max_clip_seconds = max_clip_seconds
        self.disappear_timeout = disappear_timeout
        self.clip_root_dir = os.path.join(self.base_dir, "이벤트 CLIP")
        self.person_clips = {}

        os.makedirs(self.clip_root_dir, exist_ok=True)

    def update_person(self, person_id, frame, bbox, crop_path=None):
        if frame is None or bbox is None:
            return

        state = self.person_clips.get(person_id)
        if state is None:
            state = self._create_person_state(person_id, frame)
            self.person_clips[person_id] = state

        state["last_seen"] = datetime.now()
        state["last_frame"] = frame.copy()
        state["points"].append(self._get_bbox_center(bbox))

        if crop_path is not None:
            self._copy_crop_once(state, crop_path)

        frame_size = self._get_frame_size(frame)
        if state["writer"] is None:
            self._start_new_clip(state, frame_size)

        if self._should_rotate_clip(state):
            self._close_writer(state)
            self._start_new_clip(state, frame_size)

        self._write_frame_by_wall_clock(state, frame, datetime.now())

    def finish_person(self, person_id):
        state = self.person_clips.pop(person_id, None)
        if state is None:
            return

        self._close_writer(state)
        self._save_trajectory_image(state)

    def finish_all(self):
        for person_id in list(self.person_clips.keys()):
            self.finish_person(person_id)

    def _create_person_state(self, person_id, frame):
        now = datetime.now()
        first_seen_text = now.strftime("%Y-%m-%d_%H-%M-%S")
        folder_name = f"{first_seen_text}_person{person_id}_추적영상"
        folder_path = self._get_unique_folder_path(folder_name)

        os.makedirs(folder_path, exist_ok=True)

        return {
            "person_id": person_id,
            "first_seen": now,
            "last_seen": now,
            "folder_path": folder_path,
            "clip_index": 0,
            "clip_started_at": None,
            "next_frame_time": None,
            "frames_written": 0,
            "clip_path": None,
            "writer": None,
            "points": [],
            "last_frame": frame.copy(),
            "crop_saved": False,
        }

    def _start_new_clip(self, state, frame_size):
        state["clip_index"] += 1
        state["clip_started_at"] = datetime.now()
        state["next_frame_time"] = state["clip_started_at"]
        state["frames_written"] = 0

        clip_filename = f"clip_{state['clip_index']:03d}.mp4"
        clip_path = os.path.join(state["folder_path"], clip_filename)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(clip_path, fourcc, self.fps, frame_size)

        if not writer.isOpened():
            print(f"클립 영상 Writer 생성 실패: {clip_path}")
            state["writer"] = None
            state["clip_path"] = None
            return

        state["writer"] = writer
        state["clip_path"] = clip_path

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
            print(
                "클립 영상 저장 종료: "
                f"{state.get('clip_path')} "
                f"(실시간 {wall_seconds:.2f}s, 재생예상 {encoded_seconds:.2f}s, "
                f"저장프레임 {state['frames_written']}, 실효FPS {effective_fps:.2f})"
            )
            state["next_frame_time"] = None
            state["frames_written"] = 0
            state["clip_path"] = None

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

    def _should_rotate_clip(self, state):
        if self.max_clip_seconds is None:
            return False

        if state["clip_started_at"] is None:
            return False

        elapsed_seconds = (datetime.now() - state["clip_started_at"]).total_seconds()
        return elapsed_seconds >= self.max_clip_seconds

    def _save_trajectory_image(self, state):
        frame = state.get("last_frame")
        points = state.get("points", [])

        if frame is None or len(points) == 0:
            return

        trajectory_frame = frame.copy()

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
        cv2.imwrite(save_path, trajectory_frame)

    def _copy_crop_once(self, state, crop_path):
        if state["crop_saved"]:
            return

        if not os.path.exists(crop_path):
            return

        save_path = os.path.join(state["folder_path"], "full_crop.jpg")

        try:
            shutil.copy2(crop_path, save_path)
            state["crop_saved"] = True
        except Exception as e:
            print(f"전신 crop 복사 실패: {e}")

    def _get_bbox_center(self, bbox):
        x1, y1, x2, y2 = map(int, bbox)
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def _get_frame_size(self, frame):
        height, width = frame.shape[:2]
        return width, height

    def _get_unique_folder_path(self, folder_name):
        folder_path = os.path.join(self.clip_root_dir, folder_name)

        if not os.path.exists(folder_path):
            return folder_path

        index = 2
        while True:
            candidate = os.path.join(self.clip_root_dir, f"{folder_name}_{index}")
            if not os.path.exists(candidate):
                return candidate
            index += 1
