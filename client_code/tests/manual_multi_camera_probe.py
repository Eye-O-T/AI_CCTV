"""Opt-in device probe. Reads camera registration without changing the database.

Run from client_code with the existing venv. Not part of unittest discovery.
"""
import argparse
import contextlib
import gc
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def cameras():
    connection = sqlite3.connect((ROOT / 'backend/data/ai_cctv.db').as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute('SELECT * FROM cameras WHERE is_enabled = 1')]
    finally:
        connection.close()


def endpoint_open(url):
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80 if parsed.scheme == 'http' else 554)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=3):
            return True
    except OSError:
        return False


def environment():
    import cv2
    import psutil
    import torch
    model = ROOT / 'yolo26s.pt'
    return {
        'python': platform.python_version(), 'opencv': cv2.__version__,
        'ultralytics': version('ultralytics'), 'torch': torch.__version__,
        'torch_cuda': torch.version.cuda, 'cuda_available': torch.cuda.is_available(),
        'ram_total_mb': round(psutil.virtual_memory().total / 1024**2),
        'logical_cpus': psutil.cpu_count(), 'ffmpeg_available': bool(shutil.which('ffmpeg')),
        'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest() if model.exists() else None,
        'cameras': [{'camera_id': c['camera_id'], 'kind': c['camera_type'],
                     'webcam_index': c['webcam_index'],
                     'rtsp_tcp_open': endpoint_open(c['rtsp_url']) if c['rtsp_url'] else None,
                     'edge_tcp_open': endpoint_open(c['edge_api_base_url']) if c['edge_api_base_url'] else None}
                    for c in cameras()],
    }


def summarize(values):
    if not values:
        return None
    values = sorted(values)
    return {'mean': round(statistics.mean(values), 3),
            'p95': round(values[min(len(values) - 1, int(len(values) * .95))], 3),
            'max': round(values[-1], 3)}


def run_probe(args):
    import cv2
    import numpy as np
    import psutil
    import torch
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from workers.video_worker import VideoWorker

    app = QApplication.instance() or QApplication([])
    if args.replay:
        selected = [{'camera_id': 9001 + i, 'camera_type': 'replay', 'edge_api_base_url': None}
                    for i in range(args.copies)]
    else:
        selected = [c for c in cameras() if c['camera_id'] in (args.ids or [])]
    if not 1 <= len(selected) <= 2 or (not args.replay and len(selected) != len(set(args.ids or []))):
        raise ValueError('Select one or two enabled registered camera IDs')
    base = ROOT / 'outputs' / 'stage4' / args.label
    if base.exists():
        raise ValueError('Use a new label; existing measurements are preserved')
    base.mkdir(parents=True)
    process = psutil.Process()
    process.cpu_percent()
    psutil.cpu_percent()
    started = time.perf_counter()
    samples, workers, records, gpu_samples = [], [], {}, []
    telemetry_stop = threading.Event()

    def sample_gpu():
        while not telemetry_stop.is_set():
            try:
                result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total',
                                         '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                        timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
                values = [float(v.strip()) for v in result.stdout.splitlines()[0].split(',')]
                gpu_samples.append({'elapsed': time.perf_counter() - started,
                                    'utilization_pct': values[0], 'used_mb': values[1], 'total_mb': values[2]})
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                pass
            telemetry_stop.wait(2)

    telemetry = threading.Thread(target=sample_gpu, daemon=True)
    stopping = False
    stop_started = None
    warm_at = None

    def on_frame(camera_id, frame):
        records[camera_id]['frame_times'].append(time.perf_counter() - started)
        records[camera_id]['shape'] = list(frame.shape)

    for camera in selected:
        camera_id = camera['camera_id']
        record = records[camera_id] = {'camera_id': camera_id, 'kind': camera['camera_type'],
                                     'frame_times': [], 'inference_ms': [], 'persons': [],
                                     'events': [], 'statuses': [], 'read_ms': []}
        source = str(Path(args.replay).resolve()) if args.replay else camera['webcam_index'] if camera['camera_type'] == 'webcam' else camera['rtsp_url']
        worker = VideoWorker(camera_id=camera_id, source=source,
                             edge_api_base_url=camera['edge_api_base_url'], use_yolo=args.ai,
                             use_vlm=False, ai_cctv_path=str(base / 'media'), original_segment_seconds=10)
        worker.frame_ready.connect(lambda frame, cid=camera_id: on_frame(cid, frame))
        worker.metrics_ready.connect(lambda metrics, r=record: r['persons'].append(metrics['current_objects']))
        # Store categories only; exception text may contain endpoint details.
        worker.event_ready.connect(lambda event, r=record: r['events'].append(event.get('type')))
        worker.status_ready.connect(lambda status, r=record: r['statuses'].append(status))
        read = worker.stream.read
        replay_clock = [time.perf_counter()]

        def timed_read(read=read, r=record, w=worker, replay_clock=replay_clock):
            before = time.perf_counter()
            if args.replay:
                time.sleep(max(0, replay_clock[0] - before))
                replay_clock[0] = max(replay_clock[0], time.perf_counter()) + 1 / w.stream.get_fps()
            result = read()
            if args.replay and not result[0]:
                w.stream.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                result = read()
            if result[0] and 'input_fps_reported' not in r:
                r['input_fps_reported'] = w.stream.get_fps()
            r['read_ms'].append((time.perf_counter() - before) * 1000)
            if w.tracker is not None and not getattr(w.tracker, '_probe_timed', False):
                track = w.tracker.track

                def timed_track(frame):
                    before_track = time.perf_counter()
                    result_track = track(frame)
                    r['inference_ms'].append((time.perf_counter() - before_track) * 1000)
                    predictor = getattr(w.tracker.model, 'predictor', None)
                    r['inference_device'] = str(getattr(predictor, 'device', 'unknown'))
                    return result_track

                w.tracker.track = timed_track
                w.tracker._probe_timed = True
            return result

        worker.stream.read = timed_read
        workers.append(worker)

    def tick():
        nonlocal stopping, stop_started, warm_at
        now = time.perf_counter()
        elapsed = now - started
        samples.append({'elapsed': round(elapsed, 2), 'process_cpu_pct': process.cpu_percent(),
                        'system_cpu_pct': psutil.cpu_percent(), 'rss_mb': process.memory_info().rss / 1024**2,
                        'cuda_allocated_mb': torch.cuda.memory_allocated() / 1024**2 if torch.cuda.is_available() else None})
        if warm_at is None and all(len(r['frame_times']) >= 10 for r in records.values()):
            warm_at = elapsed
            print(f'MEASUREMENT_STARTED {args.label}', flush=True)
        if not stopping and ((warm_at is not None and elapsed - warm_at >= args.seconds) or elapsed >= args.seconds + 90):
            stopping, stop_started = True, now
            for worker in workers:
                worker.request_stop()
        if all(not w.isRunning() for w in workers):
            app.quit()
        elif stopping and now - stop_started > 60:
            print('CLEANUP_STILL_PENDING', flush=True)

    timer = QTimer()
    timer.setInterval(1000)
    timer.timeout.connect(tick)
    # Avoid storing internal prints, frame contents, or device addresses in the report.
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        telemetry.start()
        for worker in workers:
            worker.start()
        timer.start()
        app.exec_()
        timer.stop()
    telemetry_stop.set()
    telemetry.join(timeout=4)
    ended = time.perf_counter()
    for worker in workers:
        worker.wait()
    app.processEvents()
    report = {'label': args.label, 'ai_requested': args.ai, 'requested_seconds': args.seconds,
              'warmup_end_seconds': warm_at, 'elapsed_seconds': ended - started,
              'stop_seconds': ended - stop_started if stop_started else None,
              'input_mode': 'local_recording_replay' if args.replay else 'registered_device',
              'samples': samples, 'gpu_samples': gpu_samples, 'cameras': []}
    for worker in workers:
        r = records[worker.camera_id]
        times = r.pop('frame_times')
        steady = [t for t in times if warm_at is not None and t >= warm_at and (stop_started is None or t <= stop_started - started)]
        r['frames'] = len(times)
        r['first_frame_seconds'] = times[0] if times else None
        r['steady_fps'] = (len(steady) - 1) / (steady[-1] - steady[0]) if len(steady) > 1 else None
        r['frame_interval_ms'] = summarize([(b - a) * 1000 for a, b in zip(steady, steady[1:])])
        r['inference_ms'] = summarize(r['inference_ms'][10:])
        r['read_ms'] = summarize(r['read_ms'])
        r['max_persons'] = max(r.pop('persons'), default=0)
        r['cleanup_ok'] = worker.cleanup_ok
        r['storage_error_count'] = len(worker.storage_errors)
        r['ai_active_at_end'] = worker.use_yolo
        r['run_directory'] = str(worker.run_directory.relative_to(base))
        r['media'] = []
        for path in worker.run_directory.rglob('*.mp4'):
            capture = cv2.VideoCapture(str(path))
            count = 0
            while capture.read()[0]:
                count += 1
            fps = capture.get(cv2.CAP_PROP_FPS)
            capture.release()
            r['media'].append({'path': str(path.relative_to(base)), 'bytes': path.stat().st_size,
                               'decoded_frames': count, 'fps': fps, 'duration': count / fps if fps else None})
        r['trajectory_images'] = [{'path': str(p.relative_to(base)),
                                   'decodable': cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR) is not None}
                                  for p in worker.run_directory.rglob('trajectory.jpg')]
        report['cameras'].append(r)
    target = base / 'report.json'
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'report': str(target), 'cameras': [{k: r[k] for k in ('camera_id', 'frames', 'steady_fps', 'inference_ms', 'inference_device', 'cleanup_ok', 'storage_error_count') if k in r} for r in report['cameras']]}, ensure_ascii=False))
    for worker in workers:
        worker.deleteLater()
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--environment', action='store_true')
    parser.add_argument('--ids', nargs='+', type=int)
    parser.add_argument('--replay', help='Existing local recording; not a live network camera')
    parser.add_argument('--copies', type=int, choices=(1, 2), default=2)
    parser.add_argument('--ai', action='store_true')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--cycles', type=int, default=1)
    parser.add_argument('--label', default=datetime.now().strftime('%Y%m%d_%H%M%S'))
    arguments = parser.parse_args()
    if arguments.seconds < 1 or not 1 <= arguments.cycles <= 10:
        parser.error('seconds must be positive and cycles must be between 1 and 10')
    if Path(arguments.label).name != arguments.label or arguments.label in ('.', '..'):
        parser.error('label must be a directory name, not a path')
    os.chdir(ROOT)
    if arguments.environment:
        print(json.dumps(environment(), ensure_ascii=False))
    else:
        import torch
        import psutil
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QCoreApplication, QEvent
        probe_app = QApplication([])
        label = arguments.label
        for cycle in range(arguments.cycles):
            arguments.label = f'{label}_cycle{cycle + 1}' if arguments.cycles > 1 else label
            report_path = run_probe(arguments)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            probe_app.processEvents()
            gc.collect()
            memory = {'rss_mb': psutil.Process().memory_info().rss / 1024**2,
                      'cuda_allocated_mb': torch.cuda.memory_allocated() / 1024**2 if torch.cuda.is_available() else None,
                      'cuda_reserved_mb': torch.cuda.memory_reserved() / 1024**2 if torch.cuda.is_available() else None}
            (report_path.parent / 'after_cleanup.json').write_text(json.dumps(memory, indent=2), encoding='utf-8')
