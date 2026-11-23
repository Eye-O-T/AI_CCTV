"""PyQt가 소유하는 서버 실행기. stdin 종료 요청으로 Uvicorn을 정상 종료한다."""

import sys
import threading
import argparse
from pathlib import Path

import uvicorn

from .main import create_app


class DesktopServer(uvicorn.Server):
    def __init__(self, config):
        super().__init__(config)
        self.parent_stopped = threading.Event()

    async def startup(self, sockets=None):
        await super().startup(sockets)
        if self.started and not self.parent_stopped.is_set():
            print("AI_CCTV_BACKEND_READY", flush=True)

    async def on_tick(self, counter):
        # 시작 중 부모가 닫혀도 startup 이후 정상 shutdown 경로를 거친다.
        return self.parent_stopped.is_set() or await super().on_tick(counter)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    server = DesktopServer(uvicorn.Config(create_app(args.db_path), host="0.0.0.0", port=args.port, access_log=False))

    def watch_parent():
        # 명시적 종료 요청 또는 부모의 파이프 종료 모두 정리한다.
        sys.stdin.readline()
        server.parent_stopped.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
