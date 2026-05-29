# 클라이언트 자원 모니터링 FastAPI 앱입니다.
# 요청을 받으면 서버의 모니터링 API로 top 요약을 요청합니다.
# 서버 주소는 환경 변수 RESOURCE_MONITOR_SERVER_URL로 바꿀 수 있습니다.

import os

import requests
from fastapi import FastAPI, HTTPException


DEFAULT_SERVER_URL = "http://127.0.0.1:8001"

app = FastAPI(title="AI CCTV Resource Monitor Client")


class ResourceMonitorClient:
    """서버 자원 모니터링 API 호출을 담당합니다.

    인자:
        server_url: 모니터링 서버의 기본 URL입니다.
        timeout_seconds: 요청 제한 시간입니다.
    반환값:
        ResourceMonitorClient 인스턴스를 반환합니다.
    """

    def __init__(self, server_url=DEFAULT_SERVER_URL, timeout_seconds=5):
        """모니터링 서버 접속 정보를 초기화합니다.

        인자:
            server_url: 모니터링 서버의 기본 URL입니다.
            timeout_seconds: 요청 제한 시간입니다.
        반환값:
            없음.
        """

        self.server_url = server_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def request_top_summary(self):
        """서버에 top 요약 정보를 요청합니다.

        인자:
            없음.
        반환값:
            서버가 반환한 JSON 딕셔너리를 반환합니다.
        """

        endpoint = f"{self.server_url}/monitor/top"
        try:
            response = requests.get(endpoint, timeout=self.timeout_seconds)
        except requests.RequestException as error:
            raise RuntimeError(f"모니터링 서버 요청 실패: {error}") from error

        if response.status_code >= 400:
            raise RuntimeError(f"모니터링 서버 오류: {response.text}")

        return response.json()


def build_monitor_client():
    """환경 변수 기준으로 모니터링 클라이언트를 생성합니다.

    인자:
        없음.
    반환값:
        ResourceMonitorClient 인스턴스를 반환합니다.
    """

    server_url = os.getenv("RESOURCE_MONITOR_SERVER_URL", DEFAULT_SERVER_URL)
    return ResourceMonitorClient(server_url=server_url)


@app.get("/monitor/top")
def request_top_summary():
    """서버 자원 모니터링 결과를 요청해 그대로 반환합니다.

    인자:
        없음.
    반환값:
        서버가 반환한 top 요약 딕셔너리를 반환합니다.
    """

    try:
        return build_monitor_client().request_top_summary()
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def main():
    """개발용 uvicorn 클라이언트 API 서버를 실행합니다.

    인자:
        없음.
    반환값:
        정상적으로는 반환하지 않습니다.
    """

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
