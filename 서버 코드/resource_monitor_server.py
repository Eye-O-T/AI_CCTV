# 서버 자원 모니터링 FastAPI 앱입니다.
# 클라이언트의 요청을 받아 서버에서 top 명령을 실행합니다.
# top 출력의 앞 5줄을 JSON 형태로 반환합니다.

import subprocess
from datetime import datetime

from fastapi import FastAPI, HTTPException


app = FastAPI(title="AI CCTV Resource Monitor Server")


class TopCommandRunner:
    """top 명령을 실행하고 결과 일부를 반환합니다.

    인자:
        timeout_seconds: top 명령 실행을 기다릴 최대 시간입니다.
    반환값:
        TopCommandRunner 인스턴스를 반환합니다.
    """

    def __init__(self, timeout_seconds=5):
        """top 명령 실행 제한 시간을 초기화합니다.

        인자:
            timeout_seconds: top 명령 실행 제한 시간입니다.
        반환값:
            없음.
        """

        self.timeout_seconds = timeout_seconds

    def read_top_lines(self, line_count=5):
        """top 명령 출력의 앞부분을 읽습니다.

        인자:
            line_count: 반환할 출력 줄 수입니다.
        반환값:
            top 출력 문자열 목록을 반환합니다.
        """

        try:
            completed_process = subprocess.run(
                ["top", "-b", "-n", "1"],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError("top 명령을 찾을 수 없습니다.") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("top 명령 실행 시간이 초과되었습니다.") from error

        if completed_process.returncode != 0:
            error_message = completed_process.stderr.strip() or "top 명령 실행 실패"
            raise RuntimeError(error_message)

        return completed_process.stdout.splitlines()[:line_count]


top_command_runner = TopCommandRunner()


@app.get("/monitor/top")
def read_top_summary():
    """서버의 top 명령 상위 5줄을 반환합니다.

    인자:
        없음.
    반환값:
        top 명령 결과와 수집 시각을 담은 딕셔너리를 반환합니다.
    """

    try:
        top_lines = top_command_runner.read_top_lines(line_count=5)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "command": "top -b -n 1",
        "line_count": len(top_lines),
        "lines": top_lines,
    }


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
