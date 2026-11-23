# network_recovery_manager.py

import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from urllib.parse import unquote


class NetworkRecoveryManager:
    def __init__(
        self,
        camera_id="cam01",
        server_url="http://라즈베리파이IP:8002/recover",
        base_dir="",
        recovery_dir="",
        min_failure_seconds=2.0,
        request_timeout=30,
        settle_seconds=2.0,
        ffmpeg_path="ffmpeg",
    ):
        self.camera_id = camera_id
        self.server_url = server_url
        self.base_dir = base_dir
        self.recovery_dir = recovery_dir or os.path.join(
            self.base_dir or os.getcwd(),
            "temp", "recovery"
        )
        self.recording_dir = os.path.join(self.base_dir, "원본 녹화본") if self.base_dir else self.recovery_dir
        self.min_failure_seconds = min_failure_seconds
        self.request_timeout = request_timeout
        self.settle_seconds = settle_seconds
        self.ffmpeg_path = ffmpeg_path

        self.failure_start_time = None
        self.requested_ranges = set()

    def has_active_failure(self):
        return self.failure_start_time is not None

    def record_failure(self, failed_time=None):
        failed_time = failed_time or datetime.now()

        if self.failure_start_time is None:
            self.failure_start_time = failed_time
            return {
                "started": True,
                "failure_start_time": self._format_time(self.failure_start_time),
            }

        return {
            "started": False,
            "failure_start_time": self._format_time(self.failure_start_time),
        }

    def record_recovery(self, recovered_time=None):
        if self.failure_start_time is None:
            return {
                "requested": False,
                "success": False,
                "reason": "no_active_failure",
            }

        recovered_time = recovered_time or datetime.now()
        failure_start_time = self.failure_start_time
        duration_seconds = (recovered_time - failure_start_time).total_seconds()
        payload = self.build_payload(failure_start_time, recovered_time)

        if duration_seconds < self.min_failure_seconds:
            self.failure_start_time = None
            return {
                "requested": False,
                "success": True,
                "skipped": True,
                "reason": "too_short",
                "duration_seconds": duration_seconds,
                "payload": payload,
            }

        request_key = self._get_request_key(payload)
        if request_key in self.requested_ranges:
            self.failure_start_time = None
            return {
                "requested": False,
                "success": True,
                "skipped": True,
                "reason": "duplicate",
                "duration_seconds": duration_seconds,
                "payload": payload,
            }

        if self.settle_seconds > 0:
            time.sleep(self.settle_seconds)

        result = self.request_recovery(payload)

        if result.get("success"):
            self.requested_ranges.add(request_key)
            self.failure_start_time = None

        result["duration_seconds"] = duration_seconds
        result["payload"] = payload
        return result

    def build_payload(self, start_time, end_time):
        start = self._format_time(start_time)
        end = self._format_time(end_time)

        return {
            "start": start,
            "end": end,
            "start_time": start,
            "end_time": end,
            "start_dt": start_time,
            "end_dt": end_time,
        }

    def request_recovery(self, payload):
        try:
            import requests

            response = requests.get(
                self.server_url,
                params={
                    "start": payload["start"],
                    "end": payload["end"],
                },
                timeout=self.request_timeout,
                stream=True,
            )
        except Exception as e:
            return {
                "requested": True,
                "success": False,
                "error": str(e),
            }

        try:
            return self._process_recovery_response(response, payload)
        finally:
            response.close()

    def _process_recovery_response(self, response, payload):
        if response.status_code == 404:
            return {
                "requested": True,
                "success": False,
                "status_code": 404,
                "reason": "not_found",
                "error": "요청한 시간 구간에 해당하는 백업 파일이 없습니다.",
            }

        if not response.ok:
            return {
                "requested": True,
                "success": False,
                "status_code": response.status_code,
                "error": response.text[:200],
            }

        try:
            zip_path = self._save_file_response(response, payload)
        except OSError as error:
            return {
                "requested": True,
                "success": False,
                "status_code": response.status_code,
                "error": f"복구 ZIP 파일 저장 실패: {error}",
            }

        try:
            merge_result = self._extract_and_merge(zip_path, payload)
        except OSError:
            merge_result = {"success": False, "error": "복구 파일 처리 실패. 임시파일을 보존합니다."}
        if not merge_result.get("success"):
            merge_result.update({
                "requested": True,
                "status_code": response.status_code,
                "zip_path": zip_path,
            })
            return merge_result

        result = {
            "requested": True,
            "success": True,
            "status_code": response.status_code,
            "saved_file": True,
            "zip_path": zip_path,
            "file_path": merge_result["file_path"],
            "ts_count": merge_result["ts_count"],
            "message": "복구 영상 MP4 파일 저장 완료",
        }
        try:
            # _save_file_response에서 이번 요청만을 위해 만든 폴더만 정리한다.
            work_dir = os.path.realpath(os.path.dirname(zip_path))
            camera_dir = os.path.realpath(os.path.join(self.recovery_dir, str(self.camera_id)))
            if os.path.dirname(work_dir) != camera_dir or not os.path.basename(work_dir).startswith("recovery_"):
                raise OSError("임시 작업 폴더 경계를 확인할 수 없습니다.")
            shutil.rmtree(work_dir)
            result["zip_path"] = None
        except OSError:
            # MP4 저장 성공은 유지한다. 정리 실패 때문에 같은 영상을 다시 만들지 않는다.
            result["cleanup_warning"] = f"임시파일 정리 실패. 확인할 폴더: {os.path.dirname(zip_path)}"
        return result

    def _save_file_response(self, response, payload):
        camera_dir = os.path.join(self.recovery_dir, str(self.camera_id))
        os.makedirs(camera_dir, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix="recovery_", dir=camera_dir)

        filename = self._get_response_filename(response)
        if not filename:
            filename = self._make_default_zip_filename(payload)

        save_path = os.path.join(work_dir, filename)

        try:
            wrote_any = False
            with open(save_path, "xb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        wrote_any = True
                        file.write(chunk)
            if not wrote_any:
                raise OSError("빈 응답입니다.")
        except Exception as error:
            raise OSError(f"임시파일 보존 위치: {work_dir}") from error

        return save_path

    def _extract_and_merge(self, zip_path, payload):
        start_dt = payload.get("start_dt") or datetime.fromisoformat(payload["start"])
        recording_dir = os.path.join(
            self.recording_dir, start_dt.strftime("%Y-%m-%d"), str(self.camera_id)
        )
        os.makedirs(recording_dir, exist_ok=True)

        work_dir = os.path.dirname(os.path.abspath(zip_path))
        ts_dir = os.path.join(work_dir, "ts")
        try:
            os.makedirs(ts_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zip_file:
                self._safe_extract(zip_file, ts_dir)
        except Exception as error:
            return {"success": False, "error": f"복구 ZIP 압축 해제 실패: {error}"}

        ts_files = self._find_ts_files(ts_dir)
        if not ts_files:
            return {"success": False, "error": "복구 ZIP 안에 TS 파일이 없습니다."}

        temporary_mp4 = os.path.join(work_dir, "merged.mp4")
        merge_result = self._merge_ts_files(ts_files, temporary_mp4, work_dir)
        if not merge_result.get("success"):
            return merge_result
        if not os.path.isfile(temporary_mp4) or os.path.getsize(temporary_mp4) == 0:
            return {"success": False, "error": "변환된 MP4가 없거나 비어 있습니다."}

        output_filename = self._make_recovered_mp4_filename(payload)
        while True:
            output_path = self._get_unique_save_path(recording_dir, output_filename)
            try:
                if os.name == "nt":
                    # Windows rename은 기존 파일을 덮어쓰지 않고 같은 볼륨에서 이동한다.
                    os.rename(temporary_mp4, output_path)
                else:
                    # POSIX rename의 덮어쓰기를 피하며 완성 파일만 원자적으로 공개한다.
                    os.link(temporary_mp4, output_path)
                break
            except FileExistsError:
                continue
            except OSError:
                return {"success": False, "error": "복구 MP4 최종 이동 실패. 임시파일을 보존합니다."}

        return {"success": True, "file_path": output_path, "ts_count": len(ts_files)}

    def _safe_extract(self, zip_file, target_dir):
        target_dir_abs = os.path.abspath(target_dir)

        for info in zip_file.infolist():
            filename = info.filename
            if not filename.lower().endswith(".ts"):
                continue

            destination = os.path.abspath(os.path.join(target_dir, filename))
            if not destination.startswith(target_dir_abs + os.sep):
                continue

            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with zip_file.open(info) as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)

    def _find_ts_files(self, folder):
        ts_files = []
        for root, _, files in os.walk(folder):
            for filename in files:
                if filename.lower().endswith(".ts"):
                    ts_files.append(os.path.join(root, filename))

        return sorted(ts_files, key=self._ts_sort_key)

    def _ts_sort_key(self, path):
        filename = os.path.basename(path)
        numbers = re.findall(r"\d+", filename)
        if numbers:
            return (filename[:filename.rfind(numbers[-1])], int(numbers[-1]))
        return (filename, int(os.path.getmtime(path)))

    def _merge_ts_files(self, ts_files, output_path, work_dir):
        concat_list_path = os.path.join(work_dir, "concat_list.txt")

        try:
            with open(concat_list_path, "w", encoding="utf-8") as list_file:
                for ts_path in ts_files:
                    safe_path = ts_path.replace("\\", "/").replace("'", "'\\''")
                    list_file.write(f"file '{safe_path}'\n")

            command = [
                self.ffmpeg_path,
                "-n",  # 동명 확인 이후 경합이 생겨도 기존 파일을 덮어쓰지 않는다.
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list_path,
                "-fflags",
                "+genpts",
                "-avoid_negative_ts",
                "make_zero",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-movflags",
                "+faststart",
                output_path,
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return {
                "success": False,
                "error": "ffmpeg 실행 파일을 찾을 수 없습니다. ffmpeg를 설치하고 PATH에 추가하세요.",
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"TS 병합 중 오류 발생: {e}",
            }

        if completed.returncode != 0:
            return {
                "success": False,
                "error": completed.stderr[-500:] or "ffmpeg 병합 실패",
            }

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return {
                "success": False,
                "error": "ffmpeg 병합 결과 파일이 생성되지 않았습니다.",
            }

        return {"success": True}

    def _get_response_filename(self, response):
        content_disposition = response.headers.get("Content-Disposition", "")

        for part in content_disposition.split(";"):
            part = part.strip()
            lower_part = part.lower()

            if lower_part.startswith("filename*="):
                filename = part.split("=", 1)[1].strip().strip('"')

                if filename.lower().startswith("utf-8''"):
                    filename = filename[7:]

                return self._sanitize_filename(unquote(filename))

            if lower_part.startswith("filename="):
                filename = part.split("=", 1)[1].strip().strip('"')
                return self._sanitize_filename(unquote(filename))

        return None

    def _make_default_zip_filename(self, payload):
        start_time = payload["start"].replace(":", "-")
        end_time = payload["end"].replace(":", "-")

        return self._sanitize_filename(
            f"recovered_backups_{self.camera_id}_{start_time}_{end_time}.zip"
        )

    def _make_recovered_mp4_filename(self, payload):
        start_dt = payload.get("start_dt") or datetime.fromisoformat(payload["start"])
        end_dt = payload.get("end_dt") or datetime.fromisoformat(payload["end"])
        start_text = start_dt.strftime("%H시%M분%S초")
        end_text = end_dt.strftime("%H시%M분%S초")
        if end_dt.date() != start_dt.date():
            end_text = end_dt.strftime("%Y-%m-%d_%H시%M분%S초")

        return self._sanitize_filename(
            f"{start_text}~{end_text}(장애복구파일).mp4"
        )

    def _get_unique_save_path(self, folder, filename):
        save_path = os.path.join(folder, filename)

        if not os.path.exists(save_path):
            return save_path

        name, ext = os.path.splitext(filename)
        index = 2

        while True:
            marker = "(장애복구파일)"
            if name.endswith(marker):
                candidate_name = f"{name[:-len(marker)]}_{index}{marker}{ext}"
            else:
                candidate_name = f"{name}_{index}{ext}"
            candidate = os.path.join(folder, candidate_name)

            if not os.path.exists(candidate):
                return candidate

            index += 1

    def _get_request_key(self, payload):
        return (
            self.camera_id,
            payload["start"],
            payload["end"],
        )

    def _format_time(self, value):
        return value.replace(microsecond=0).isoformat()

    def _sanitize_filename(self, filename):
        filename = os.path.basename(filename)
        return re.sub(r'[<>:"/\\|?*]', "_", filename)
