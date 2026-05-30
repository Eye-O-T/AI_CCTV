# network_recovery_manager.py

import os
import re
from datetime import datetime
from urllib.parse import unquote


class NetworkRecoveryManager:
    def __init__(
        self,
        camera_id="cam01",
        server_url="http://라즈베리파이IP:8002/recover",
        recovery_dir="",
        min_failure_seconds=2.0,
        request_timeout=5,
    ):
        self.camera_id = camera_id
        self.server_url = server_url
        self.recovery_dir = recovery_dir or os.path.join(os.getcwd(), "복구 영상")
        self.min_failure_seconds = min_failure_seconds
        self.request_timeout = request_timeout

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
        self.failure_start_time = None

        duration_seconds = (recovered_time - failure_start_time).total_seconds()
        payload = self.build_payload(failure_start_time, recovered_time)

        if duration_seconds < self.min_failure_seconds:
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
            return {
                "requested": False,
                "success": True,
                "skipped": True,
                "reason": "duplicate",
                "duration_seconds": duration_seconds,
                "payload": payload,
            }

        result = self.request_recovery(payload)

        if result.get("success"):
            self.requested_ranges.add(request_key)

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
            )
        except Exception as e:
            return {
                "requested": True,
                "success": False,
                "error": str(e),
            }

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

        if not response.content:
            return {
                "requested": True,
                "success": False,
                "status_code": response.status_code,
                "error": "서버 응답에 복구 영상 파일 데이터가 없습니다.",
            }

        save_path = self._save_file_response(response, payload)

        if save_path is None:
            return {
                "requested": True,
                "success": False,
                "status_code": response.status_code,
                "error": "복구 영상 파일 저장 실패",
            }

        return {
            "requested": True,
            "success": True,
            "status_code": response.status_code,
            "saved_file": True,
            "file_path": save_path,
            "message": "복구 영상 ZIP 파일 저장 완료",
        }

    def _save_file_response(self, response, payload):
        os.makedirs(self.recovery_dir, exist_ok=True)

        filename = self._get_response_filename(response)
        if not filename:
            filename = self._make_default_filename(payload)

        save_path = self._get_unique_save_path(filename)

        try:
            with open(save_path, "wb") as file:
                file.write(response.content)
        except Exception:
            return None

        return save_path

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

    def _make_default_filename(self, payload):
        start_time = payload["start"].replace(":", "-")
        end_time = payload["end"].replace(":", "-")

        return self._sanitize_filename(
            f"recovered_backups_{self.camera_id}_{start_time}_{end_time}.zip"
        )

    def _get_unique_save_path(self, filename):
        save_path = os.path.join(self.recovery_dir, filename)

        if not os.path.exists(save_path):
            return save_path

        name, ext = os.path.splitext(filename)
        index = 2

        while True:
            candidate = os.path.join(self.recovery_dir, f"{name}_{index}{ext}")

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