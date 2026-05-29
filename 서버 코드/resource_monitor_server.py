# 서버 자원 모니터링 FastAPI 앱입니다.
# 클라이언트 요청을 받아 서버의 CPU와 메모리 사용률을 반환합니다.
# 임시 모니터링 대상 프로세스는 현재 FastAPI 서버 프로세스입니다.

import os
from datetime import datetime

import psutil
from fastapi import FastAPI, HTTPException


app = FastAPI(title="AI CCTV Resource Monitor Server")


class ResourceUsageCollector:
    """서버와 특정 프로세스의 자원 사용률을 수집합니다.

    인자:
        process_id: 모니터링할 프로세스 ID입니다.
        sample_interval_seconds: CPU 사용률 샘플링 시간입니다.
    반환값:
        ResourceUsageCollector 인스턴스를 반환합니다.
    """

    def __init__(self, process_id=None, sample_interval_seconds=0.1):
        """자원 사용률 수집 대상을 초기화합니다.

        인자:
            process_id: 모니터링할 프로세스 ID이며 없으면 현재 프로세스입니다.
            sample_interval_seconds: CPU 사용률을 계산할 샘플링 시간입니다.
        반환값:
            없음.
        """

        self.process_id = process_id if process_id is not None else os.getpid()
        self.sample_interval_seconds = sample_interval_seconds

    def collect(self):
        """전체 시스템과 대상 프로세스의 자원 사용률을 수집합니다.

        인자:
            없음.
        반환값:
            CPU와 메모리 사용률을 담은 딕셔너리를 반환합니다.
        """

        process = self._get_process()
        return {
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "cpu": {
                "total_percent": psutil.cpu_percent(
                    interval=self.sample_interval_seconds
                ),
            },
            "memory": {
                "total_percent": psutil.virtual_memory().percent,
            },
            "process": {
                "pid": self.process_id,
                "name": process.name(),
                "cpu_percent": process.cpu_percent(
                    interval=self.sample_interval_seconds
                ),
                "memory_percent": process.memory_percent(),
            },
        }

    def _get_process(self):
        """모니터링 대상 프로세스 객체를 반환합니다.

        인자:
            없음.
        반환값:
            psutil.Process 객체를 반환합니다.
        """

        try:
            return psutil.Process(self.process_id)
        except psutil.NoSuchProcess as error:
            raise RuntimeError(f"프로세스를 찾을 수 없습니다: {self.process_id}") from error
        except psutil.AccessDenied as error:
            raise RuntimeError(f"프로세스 접근 권한이 없습니다: {self.process_id}") from error


resource_usage_collector = ResourceUsageCollector()


@app.get("/monitor/top")
def read_resource_usage():
    """서버 자원 사용률 JSON을 반환합니다.

    인자:
        없음.
    반환값:
        전체 CPU, 전체 메모리, 대상 프로세스 사용률 딕셔너리를 반환합니다.
    """

    try:
        return resource_usage_collector.collect()
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


def main():
    """개발용 uvicorn 서버를 실행합니다.

    인자:
        없음.
    반환값:
        정상적으로는 반환하지 않습니다.
    """

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
