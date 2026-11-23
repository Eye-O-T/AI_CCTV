import os
import time
from datetime import datetime

import psutil
from fastapi import FastAPI, HTTPException


app = FastAPI(title="AI CCTV Resource Monitor Server")

DEFAULT_PROCESS_KEYWORDS = (
    "stream_and_record.sh",
    "ffmpeg",
    "gst-launch",
    "gstreamer",
    "libcamera",
    "rpicam",
    "backup_api_server",
    "resource_monitor",
    "ai_cctv",
)


class ResourceUsageCollector:
    def __init__(self, sample_interval_seconds=0.1):
        self.sample_interval_seconds = sample_interval_seconds

    def collect(self):
        processes = self._collect_target_processes()

        for process in processes:
            try:
                process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        time.sleep(self.sample_interval_seconds)

        cpu_total_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count() or 1
        process_cpu_percent = self._sum_process_cpu_percent(processes) / cpu_count
        process_cpu_percent = min(100.0, max(0.0, process_cpu_percent))

        memory = psutil.virtual_memory()
        process_memory_bytes = self._sum_process_memory_bytes(processes)
        other_memory_bytes = max(0, memory.used - process_memory_bytes)

        disk_path = os.getenv("AI_CCTV_DISK_PATH", os.getcwd())
        if not os.path.exists(disk_path):
            disk_path = os.getcwd()
        disk = psutil.disk_usage(disk_path)

        return {
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "process": {
                "count": len(processes),
                "pids": [process.pid for process in processes],
                "keywords": self._get_process_keywords(),
            },
            "cpu": {
                "total_percent": cpu_total_percent,
                "app_percent": process_cpu_percent,
                "other_percent": max(0.0, cpu_total_percent - process_cpu_percent),
                "idle_percent": max(0.0, 100.0 - cpu_total_percent),
            },
            "memory": {
                "used_percent": memory.percent,
                "total_gb": self._bytes_to_gb(memory.total),
                "available_gb": self._bytes_to_gb(memory.available),
                "app_gb": self._bytes_to_gb(process_memory_bytes),
                "other_gb": self._bytes_to_gb(other_memory_bytes),
            },
            "disk": {
                "path": disk_path,
                "used_percent": disk.percent,
                "total_gb": self._bytes_to_gb(disk.total),
                "used_gb": self._bytes_to_gb(disk.used),
                "free_gb": self._bytes_to_gb(disk.free),
            },
        }

    def _collect_target_processes(self):
        matched_pids = set()

        for pid in self._get_env_pids():
            try:
                matched_pids.add(psutil.Process(pid).pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                continue

        keywords = [keyword.lower() for keyword in self._get_process_keywords()]
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = process.info.get("name") or ""
                cmdline = " ".join(process.info.get("cmdline") or [])
                haystack = f"{name} {cmdline}".lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            if any(keyword in haystack for keyword in keywords):
                matched_pids.add(process.pid)

        all_pids = set(matched_pids)
        for pid in list(matched_pids):
            try:
                for child in psutil.Process(pid).children(recursive=True):
                    all_pids.add(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        processes = []
        for pid in sorted(all_pids):
            try:
                processes.append(psutil.Process(pid))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return processes

    def _get_env_pids(self):
        raw_pids = os.getenv("AI_CCTV_MONITOR_PIDS", "")
        pids = []
        for value in raw_pids.split(","):
            value = value.strip()
            if value.isdigit():
                pids.append(int(value))
        return pids

    def _get_process_keywords(self):
        raw_keywords = os.getenv("AI_CCTV_PROCESS_KEYWORDS", "")
        if not raw_keywords.strip():
            return list(DEFAULT_PROCESS_KEYWORDS)
        return [
            keyword.strip()
            for keyword in raw_keywords.split(",")
            if keyword.strip()
        ]

    def _sum_process_cpu_percent(self, processes):
        total = 0.0
        for process in processes:
            try:
                total += process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total

    def _sum_process_memory_bytes(self, processes):
        total = 0
        for process in processes:
            try:
                total += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total

    def _bytes_to_gb(self, value):
        return value / (1024 ** 3)


resource_usage_collector = ResourceUsageCollector()


@app.get("/monitor/resources")
def read_resource_usage():
    try:
        return resource_usage_collector.collect()
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/monitor/top")
def read_legacy_resource_usage():
    return read_resource_usage()


def main():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
