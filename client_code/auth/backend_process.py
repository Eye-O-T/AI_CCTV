"""클라이언트가 생성한 FastAPI 프로세스만 시작·종료한다."""

from pathlib import Path
import sys

from PyQt5.QtCore import QObject, QProcess, QTimer, pyqtSignal


class BackendProcess(QObject):
    ready = pyqtSignal()
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, parent=None, db_path=None, port=8000):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        self.is_ready = False
        self.stopping = False
        self.buffer = b""
        self.arguments = ["-u", "-m", "client_code.backend.desktop_server", "--port", str(port)]
        if db_path is not None:
            self.arguments.extend(["--db-path", str(db_path)])

    def start(self):
        if self.process.state() != QProcess.NotRunning:
            if self.is_ready:
                self.ready.emit()
            return
        self.stopping = False
        self.is_ready = False
        self.buffer = b""
        self.process.start(sys.executable, self.arguments)
        self.timer.start(30000)

    def _read(self):
        self.buffer = (self.buffer + bytes(self.process.readAllStandardOutput()))[-8192:]
        if not self.is_ready and not self.stopping and b"AI_CCTV_BACKEND_READY" in self.buffer:
            self.is_ready = True
            self.timer.stop()
            self.ready.emit()

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self.timer.stop()
            if self.stopping:
                self.stopped.emit()
            else:
                self.failed.emit("백엔드를 시작할 수 없습니다. Python 가상환경과 패키지를 확인해 주세요.")

    def _finished(self, *_):
        self.timer.stop()
        self.is_ready = False
        if self.stopping:
            self.stopped.emit()
        else:
            self.failed.emit("백엔드가 종료되었습니다. 8000 포트의 기존 서버 또는 실행 환경을 확인한 뒤 다시 연결해 주세요.")

    def stop(self):
        self.stopping = True
        self.is_ready = False
        if self.process.state() == QProcess.NotRunning:
            self.stopped.emit()
            return
        self.process.write(b"stop\n")
        self.process.closeWriteChannel()
        self.timer.start(10000)

    def _timeout(self):
        if self.stopping:
            # 정상 종료 시간 초과 시에도 자신이 만든 자식만 종료한다.
            self.process.kill()
        else:
            self.failed.emit("백엔드 준비 시간이 초과되었습니다. 종료 후 다시 실행해 주세요.")
            self.stop()
