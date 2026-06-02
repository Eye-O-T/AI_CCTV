import ctypes
import os
import subprocess
import time
from ctypes import wintypes

import psutil
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class SparklineChart(QWidget):
    def __init__(self, color="#38bdf8", parent=None):
        super().__init__(parent)
        self.values = []
        self.max_points = 60
        self.color = QColor(color)
        self.setMinimumHeight(78)

    def add_value(self, value):
        self.values.append(max(0.0, min(100.0, float(value))))
        if len(self.values) > self.max_points:
            self.values = self.values[-self.max_points:]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)

        painter.fillRect(self.rect(), QColor("#0f172a"))
        painter.setPen(QPen(QColor("#334155"), 1))
        painter.drawRect(rect)

        if len(self.values) < 2:
            return

        step = rect.width() / max(1, self.max_points - 1)
        start_index = self.max_points - len(self.values)
        points = []
        for i, value in enumerate(self.values):
            x = rect.left() + (start_index + i) * step
            y = rect.bottom() - (value / 100.0) * rect.height()
            points.append((x, y))

        painter.setPen(QPen(self.color, 2))
        for i in range(1, len(points)):
            painter.drawLine(
                int(points[i - 1][0]),
                int(points[i - 1][1]),
                int(points[i][0]),
                int(points[i][1]),
            )


class ResourceCard(QFrame):
    def __init__(self, title, accent="#38bdf8", parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background-color: #1e293b; border-radius: 8px; }"
            "QLabel { background: transparent; }"
            "QProgressBar { background-color: #0f172a; border: none; "
            "border-radius: 4px; height: 10px; text-align: center; }"
            "QProgressBar::chunk { background-color: "
            + accent
            + "; border-radius: 4px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.value_label = QLabel("-")
        self.value_label.setAlignment(Qt.AlignRight)
        self.value_label.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {accent};"
        )
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.value_label)
        layout.addLayout(header_layout)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.detail_label = QLabel("-")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self.detail_label)

        self.chart = SparklineChart(accent)
        layout.addWidget(self.chart)

    def update_data(self, percent, value_text, detail_text):
        safe_percent = 0 if percent is None else max(0, min(100, int(percent)))
        self.progress.setValue(safe_percent)
        self.value_label.setText(value_text)
        self.detail_label.setText(detail_text)
        self.chart.add_value(safe_percent)


class WindowsCpuUtilityCounter:
    PDH_FMT_DOUBLE = 0x00000200
    ERROR_SUCCESS = 0

    class PdhFmtCounterValue(ctypes.Structure):
        _fields_ = [
            ("CStatus", wintypes.DWORD),
            ("doubleValue", ctypes.c_double),
        ]

    def __init__(self):
        self.enabled = False
        self.query = wintypes.HANDLE()
        self.counter = wintypes.HANDLE()
        self.pdh = None

        if os.name != "nt":
            return

        try:
            self.pdh = ctypes.WinDLL("pdh.dll")
            if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != self.ERROR_SUCCESS:
                return

            counter_path = r"\Processor Information(_Total)\% Processor Utility"
            add_counter = getattr(self.pdh, "PdhAddEnglishCounterW", None)
            if add_counter is None:
                return

            if add_counter(
                self.query,
                counter_path,
                0,
                ctypes.byref(self.counter),
            ) != self.ERROR_SUCCESS:
                return

            self.pdh.PdhCollectQueryData(self.query)
            self.enabled = True
        except (AttributeError, OSError):
            self.close()

    def read(self):
        if not self.enabled:
            return None

        value = self.PdhFmtCounterValue()
        try:
            if self.pdh.PdhCollectQueryData(self.query) != self.ERROR_SUCCESS:
                return None
            if self.pdh.PdhGetFormattedCounterValue(
                self.counter,
                self.PDH_FMT_DOUBLE,
                None,
                ctypes.byref(value),
            ) != self.ERROR_SUCCESS:
                return None
        except OSError:
            return None

        return max(0.0, min(100.0, float(value.doubleValue)))

    def close(self):
        if self.pdh is not None and self.query:
            try:
                self.pdh.PdhCloseQuery(self.query)
            except OSError:
                pass
        self.enabled = False


class ResourceMonitorWindow(QDialog):
    def __init__(self, parent=None, storage_path=""):
        super().__init__(parent)
        self.storage_path = storage_path or os.getcwd()
        self.process = psutil.Process(os.getpid())
        self.last_net = psutil.net_io_counters()
        self.last_net_time = time.time()
        self.last_gpu_update = 0
        self.gpu_cache = None
        self.cpu_utility_counter = WindowsCpuUtilityCounter()
        self.monitor_view = "pc"

        self.setWindowTitle("리소스 모니터링")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(960, 720)
        self.setStyleSheet(
            "background-color: #0f172a; color: #f8fafc; font-family: Arial;"
        )

        self._prime_cpu_counters()
        self.init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel("리소스 모니터링")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        self.summary_label = QLabel("실시간 시스템 상태를 수집 중입니다.")
        self.summary_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        layout.addWidget(self.summary_label)

        self.monitor_stack = QStackedWidget()
        layout.addWidget(self.monitor_stack, stretch=1)

        pc_page = QWidget()
        grid = QGridLayout(pc_page)
        grid.setSpacing(14)
        grid.setContentsMargins(0, 0, 0, 0)

        self.cpu_card = ResourceCard("CPU", "#22c55e")
        self.ram_card = ResourceCard("RAM", "#38bdf8")
        self.gpu_card = ResourceCard("GPU", "#facc15")
        self.vram_card = ResourceCard("VRAM", "#a78bfa")
        self.disk_card = ResourceCard("저장 공간", "#fb7185")
        self.network_card = ResourceCard("네트워크", "#2dd4bf")

        grid.addWidget(self.cpu_card, 0, 0)
        grid.addWidget(self.ram_card, 0, 1)
        grid.addWidget(self.gpu_card, 1, 0)
        grid.addWidget(self.vram_card, 1, 1)
        grid.addWidget(self.disk_card, 2, 0)
        grid.addWidget(self.network_card, 2, 1)

        smart_page = QFrame()
        smart_page.setStyleSheet("background-color: #1e293b; border-radius: 8px;")
        smart_layout = QVBoxLayout(smart_page)
        smart_layout.setContentsMargins(24, 24, 24, 24)
        smart_layout.setSpacing(10)
        smart_layout.addStretch()

        smart_title = QLabel("스마트CCTV 모니터링 정보")
        smart_title.setAlignment(Qt.AlignCenter)
        smart_title.setStyleSheet("font-size: 24px; font-weight: bold;")
        smart_layout.addWidget(smart_title)

        smart_status = QLabel("연결 대기")
        smart_status.setAlignment(Qt.AlignCenter)
        smart_status.setStyleSheet("color: #94a3b8; font-size: 16px;")
        smart_layout.addWidget(smart_status)
        smart_layout.addStretch()

        self.monitor_stack.addWidget(pc_page)
        self.monitor_stack.addWidget(smart_page)

        self.switch_monitor_button = QPushButton("스마트CCTV 모니터링 정보")
        self.switch_monitor_button.setMinimumHeight(78)
        self.switch_monitor_button.setStyleSheet(
            "QPushButton { background-color: #0e7490; color: white; "
            "border: none; border-radius: 8px; font-size: 22px; "
            "font-weight: bold; }"
            "QPushButton:hover { background-color: #0891b2; }"
            "QPushButton:pressed { background-color: #155e75; }"
        )
        self.switch_monitor_button.clicked.connect(self.switch_monitor_view)
        layout.addWidget(self.switch_monitor_button)

    def switch_monitor_view(self):
        if self.monitor_view == "pc":
            self.monitor_view = "smart"
            self.monitor_stack.setCurrentIndex(1)
            self.switch_monitor_button.setText("사용자PC 모니터링 정보")
            self.summary_label.setText("스마트CCTV 리소스 정보 연결 대기")
            return

        self.monitor_view = "pc"
        self.monitor_stack.setCurrentIndex(0)
        self.switch_monitor_button.setText("스마트CCTV 모니터링 정보")
        self.refresh()

    def _prime_cpu_counters(self):
        psutil.cpu_percent(interval=None)
        self.process.cpu_percent(interval=None)
        for child in self.process.children(recursive=True):
            try:
                child.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def refresh(self):
        if self.monitor_view == "smart":
            self.summary_label.setText("스마트CCTV 리소스 정보 연결 대기")
            return

        cpu = self._collect_cpu()
        memory = self._collect_memory()
        disk = self._collect_disk()
        network = self._collect_network()
        gpu = self._collect_gpu()

        self.cpu_card.update_data(
            cpu["total_percent"],
            f"{cpu['total_percent']:.0f}%",
            (
                f"우리 프로세스 {cpu['app_percent']:.1f}% · "
                f"기타 {cpu['other_percent']:.1f}% · "
                f"가용 {cpu['idle_percent']:.1f}%"
            ),
        )
        self.ram_card.update_data(
            memory["used_percent"],
            f"{memory['available_gb']:.1f}GB 여유",
            (
                f"총 {memory['total_gb']:.1f}GB · "
                f"우리 {memory['app_gb']:.2f}GB · "
                f"기타 {memory['other_gb']:.1f}GB"
            ),
        )
        self.disk_card.update_data(
            disk["used_percent"],
            f"{disk['free_gb']:.1f}GB 여유",
            f"대상 경로: {disk['path']} · 총 {disk['total_gb']:.1f}GB",
        )
        self.network_card.update_data(
            network["percent_hint"],
            f"↓ {network['recv_mbps']:.2f} / ↑ {network['sent_mbps']:.2f} Mbps",
            "최근 1초 기준 송수신 속도",
        )

        if gpu is None:
            self.gpu_card.update_data(0, "지원 안 됨", "NVIDIA GPU 정보를 찾지 못했습니다.")
            self.vram_card.update_data(0, "지원 안 됨", "VRAM 사용량을 가져올 수 없습니다.")
        else:
            vram_total_gb = self._mb_to_gb(gpu["vram_total_mb"])
            vram_used_gb = self._mb_to_gb(gpu["vram_used_mb"])
            vram_free_gb = self._mb_to_gb(gpu["vram_free_mb"])
            vram_detail = (
                f"총 {vram_total_gb:.1f}GB · "
                f"사용 {vram_used_gb:.1f}GB · 프로세스별 측정 불가"
            )
            if gpu["app_vram_mb"] is not None:
                vram_detail = (
                    f"총 {vram_total_gb:.1f}GB · "
                    f"우리 {self._mb_to_gb(gpu['app_vram_mb']):.1f}GB · "
                    f"기타 {self._mb_to_gb(gpu['other_vram_mb']):.1f}GB"
                )

            self.gpu_card.update_data(
                gpu["util_percent"],
                f"{gpu['util_percent']:.0f}%",
                f"GPU {gpu['gpu_count']}개 · 온도 {gpu['max_temp_text']}",
            )
            self.vram_card.update_data(
                gpu["vram_percent"],
                f"{vram_free_gb:.1f}GB 여유",
                vram_detail,
            )

        self.summary_label.setText(
            f"업데이트: {time.strftime('%H:%M:%S')} · "
            "수집 주기 1초 · GPU 정보는 약 3초마다 갱신"
        )

    def _collect_cpu(self):
        total_percent = self.cpu_utility_counter.read()
        if total_percent is None:
            total_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count() or 1
        app_percent = self._process_cpu_percent() / cpu_count
        app_percent = min(100.0, max(0.0, app_percent))
        other_percent = max(0.0, total_percent - app_percent)
        idle_percent = max(0.0, 100.0 - total_percent)
        return {
            "total_percent": total_percent,
            "app_percent": app_percent,
            "other_percent": other_percent,
            "idle_percent": idle_percent,
        }

    def _mb_to_gb(self, value):
        return float(value) / 1024.0

    def _process_cpu_percent(self):
        total = 0.0
        processes = [self.process] + self.process.children(recursive=True)
        for proc in processes:
            try:
                total += proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total

    def _collect_memory(self):
        vm = psutil.virtual_memory()
        app_bytes = self._process_memory_bytes()
        other_bytes = max(0, vm.used - app_bytes)
        return {
            "used_percent": vm.percent,
            "total_gb": vm.total / (1024 ** 3),
            "available_gb": vm.available / (1024 ** 3),
            "app_gb": app_bytes / (1024 ** 3),
            "other_gb": other_bytes / (1024 ** 3),
        }

    def _process_memory_bytes(self):
        total = 0
        processes = [self.process] + self.process.children(recursive=True)
        for proc in processes:
            try:
                total += proc.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total

    def _collect_disk(self):
        path = self.storage_path
        if not os.path.exists(path):
            path = os.getcwd()
        usage = psutil.disk_usage(path)
        return {
            "path": path,
            "used_percent": usage.percent,
            "total_gb": usage.total / (1024 ** 3),
            "free_gb": usage.free / (1024 ** 3),
        }

    def _collect_network(self):
        current = psutil.net_io_counters()
        current_time = time.time()
        elapsed = max(0.001, current_time - self.last_net_time)
        sent_mbps = (
            (current.bytes_sent - self.last_net.bytes_sent) * 8 / elapsed / 1_000_000
        )
        recv_mbps = (
            (current.bytes_recv - self.last_net.bytes_recv) * 8 / elapsed / 1_000_000
        )
        self.last_net = current
        self.last_net_time = current_time
        return {
            "sent_mbps": max(0.0, sent_mbps),
            "recv_mbps": max(0.0, recv_mbps),
            "percent_hint": min(100, int((sent_mbps + recv_mbps) * 2)),
        }

    def _collect_gpu(self):
        if time.time() - self.last_gpu_update < 3 and self.gpu_cache is not None:
            return self.gpu_cache

        gpu_rows = self._run_nvidia_smi([
            "--query-gpu=utilization.gpu,memory.total,memory.used,temperature.gpu",
            "--format=csv,noheader,nounits",
        ])
        if not gpu_rows:
            self.gpu_cache = None
            self.last_gpu_update = time.time()
            return None

        gpu_count = 0
        util_total = 0.0
        vram_total = 0.0
        vram_used = 0.0
        temps = []
        for row in gpu_rows:
            parts = [part.strip() for part in row.split(",")]
            if len(parts) < 4:
                continue
            try:
                util_total += float(parts[0])
                vram_total += float(parts[1])
                vram_used += float(parts[2])
                temps.append(float(parts[3]))
                gpu_count += 1
            except ValueError:
                continue

        if gpu_count == 0:
            return None

        app_vram = self._collect_app_vram_mb()
        other_vram = None
        if app_vram is not None:
            app_vram = min(app_vram, vram_used)
            other_vram = max(0.0, vram_used - app_vram)
        max_temp = max(temps) if temps else None

        self.gpu_cache = {
            "gpu_count": gpu_count,
            "util_percent": util_total / gpu_count,
            "vram_percent": (vram_used / vram_total * 100.0) if vram_total else 0.0,
            "vram_total_mb": vram_total,
            "vram_used_mb": vram_used,
            "vram_free_mb": max(0.0, vram_total - vram_used),
            "app_vram_mb": app_vram,
            "other_vram_mb": other_vram,
            "max_temp": max_temp,
            "max_temp_text": f"{max_temp:.0f}°C" if max_temp is not None else "-",
        }
        self.last_gpu_update = time.time()
        return self.gpu_cache

    def _collect_app_vram_mb(self):
        app_pids = {self.process.pid}
        for child in self.process.children(recursive=True):
            try:
                app_pids.add(child.pid)
            except psutil.NoSuchProcess:
                pass

        rows = self._run_nvidia_smi([
            "--query-compute-apps=pid,used_memory",
            "--format=csv,noheader,nounits",
        ])
        has_matching_pid = False
        total = 0.0
        for row in rows:
            parts = [part.strip() for part in row.split(",")]
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid not in app_pids:
                continue

            has_matching_pid = True
            try:
                total += float(parts[1])
            except ValueError:
                return None
        if has_matching_pid:
            return total
        if rows:
            return None
        return total

    def _run_nvidia_smi(self, args):
        try:
            result = subprocess.run(
                ["nvidia-smi", *args],
                capture_output=True,
                text=True,
                timeout=1.5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return []

        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _collect_temperature(self, gpu):
        values = []
        try:
            sensors = psutil.sensors_temperatures()
        except (AttributeError, OSError):
            sensors = {}

        for name, entries in sensors.items():
            for entry in entries:
                if entry.current is not None:
                    values.append((name, float(entry.current)))

        if gpu is not None and gpu.get("max_temp") is not None:
            values.append(("GPU", float(gpu["max_temp"])))

        if not values:
            return None

        max_name, max_temp = max(values, key=lambda item: item[1])
        percent_hint = max(0, min(100, int(max_temp / 100.0 * 100)))
        return {
            "max_celsius": max_temp,
            "percent_hint": percent_hint,
            "detail": f"최고 온도 센서: {max_name}",
        }

    def closeEvent(self, event):
        self.timer.stop()
        self.cpu_utility_counter.close()
        event.accept()
