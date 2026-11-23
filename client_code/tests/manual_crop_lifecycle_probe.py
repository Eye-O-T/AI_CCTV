"""실제 두 웹캠의 수신·YOLO·crop 스레드 STOP/재시작 진단. DB/API는 사용하지 않는다.

저장 위치: 프로젝트 루트 output/crop_stage2_<label>/ (crop과 JSON만 저장).
원본 녹화·클립은 끄며 FullBodyChecker와 품질 기준은 제품 기본값을 사용한다.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--indices', nargs=2, type=int, required=True)
    parser.add_argument('--seconds', type=float, default=8, help='첫 프레임 후 각 관측 구간 길이')
    parser.add_argument('--label', default=datetime.now().strftime('%Y%m%d_%H%M%S'))
    args = parser.parse_args()
    if args.seconds <= 0 or len(set(args.indices)) != 2 or min(args.indices) < 0:
        parser.error('서로 다른 두 장치 번호와 양수 seconds가 필요합니다.')
    if not args.label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.label):
        parser.error('label은 영문·숫자·밑줄·하이픈만 허용합니다.')
    base = ROOT.parent / 'output' / f'crop_stage2_{args.label}'
    base.mkdir(parents=True, exist_ok=False)
    os.chdir(ROOT)  # 기존 YOLO 가중치 상대 경로 유지

    import cv2
    import numpy as np
    from PyQt5.QtCore import QCoreApplication, QTimer
    from workers.video_worker import VideoWorker

    app = QCoreApplication([])
    workers, records = [], []
    started = time.monotonic()
    phase = 'initial'
    phase_at = started
    stopping_at = None
    other_before_stop = None
    failure = None

    def launch(camera_id, index):
        worker = VideoWorker(camera_id=camera_id, source=index, use_vlm=False)
        worker.crop_worker.collector.output_root = base
        record = dict(camera_id=camera_id, source_index=index, frames=[], persons=[],
                      errors=[], saved_paths=[], save_ms=[])
        workers.append(worker)
        records.append(record)
        worker.frame_ready.connect(lambda frame: record['frames'].append(time.monotonic() - started))
        worker.metrics_ready.connect(lambda m: record['persons'].append(m['current_objects']))
        # 에러 전문에는 장치 주소 등 내부 정보가 있을 수 있어 범주만 기록한다.
        worker.event_ready.connect(lambda e: record['errors'].append(e['type']) if e['type'] == 'error' else None)
        save = worker.crop_worker.collector._save

        def measured_save(*items):
            before = time.monotonic()
            paths = save(*items)
            record['save_ms'].append(round((time.monotonic() - before) * 1000, 3))
            record['saved_paths'].extend(str(p.relative_to(base)) for p in paths)
            return paths

        worker.crop_worker.collector._save = measured_save
        worker.start()
        return worker

    def stop_all():
        nonlocal phase, stopping_at
        phase = 'stopping'
        if stopping_at is None:
            stopping_at = time.monotonic()
        for worker in workers:
            worker.request_stop()

    def tick():
        nonlocal phase, phase_at, other_before_stop, failure
        now = time.monotonic()
        if now - started > 90 and phase != 'stopping':
            failure = 'diagnostic timeout'
            stop_all()
        if phase == 'initial' and all(r['frames'] for r in records[:2]):
            phase, phase_at = 'both_running', now
        elif phase == 'both_running' and now - phase_at >= args.seconds:
            other_before_stop = len(records[1]['frames'])
            workers[0].request_stop()
            phase, phase_at = 'first_stopping', now
        elif phase == 'first_stopping' and not workers[0].isRunning():
            if workers[0].crop_worker.is_alive():
                failure = 'crop thread still running after VideoWorker finished'
                stop_all()
            elif len(records[1]['frames']) >= other_before_stop + 5:
                launch(1, args.indices[0])
                phase, phase_at = 'restarting', now
        elif phase == 'restarting' and records[2]['frames']:
            phase, phase_at = 'restarted_running', now
        elif phase == 'restarted_running' and now - phase_at >= args.seconds:
            stop_all()
        expected = workers if phase in ('initial', 'both_running') else workers[1:]
        if phase != 'stopping' and any(not w.isRunning() for w in expected):
            failure = 'unexpected VideoWorker finish'
            stop_all()
        if phase == 'stopping' and all(not w.isRunning() for w in workers):
            timer.stop()
            app.quit()

    launch(1, args.indices[0])
    launch(2, args.indices[1])
    timer = QTimer()
    timer.setInterval(30)
    timer.timeout.connect(tick)
    timer.start()
    app.exec_()
    summary = []
    for worker, record in zip(workers, records):
        gaps = [(b - a) * 1000 for a, b in zip(record['frames'], record['frames'][1:])]
        summary.append(dict(camera_id=record['camera_id'], source_index=record['source_index'],
            frames=len(record['frames']), max_persons=max(record['persons'], default=0),
            gap_mean_ms=round(statistics.mean(gaps), 3) if gaps else None,
            gap_max_ms=round(max(gaps), 3) if gaps else None,
            saved_paths=record['saved_paths'], save_ms=record['save_ms'], errors=record['errors'],
            cleanup_ok=worker.cleanup_ok, crop_thread_stopped=not worker.crop_worker.is_alive()))
    files = list(base.rglob('*.jpg'))
    report = dict(failure=failure, runs=summary,
        second_frames_after_first_stop=(len(records[1]['frames']) - other_before_stop)
            if other_before_stop is not None else None,
        restart_observed=len(records) == 3 and bool(records[2]['frames']),
        jpeg_count=len(files), decoded_jpegs=sum(
            cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8), 1) is not None for p in files),
        note='Actual camera capture and YOLO; no GUI interaction, DB/API, recording or clips. '
             'Gap metrics include initialization and restart; this is not a before/after FPS comparison.')
    (base / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 1 if failure or not report['restart_observed'] or any(
        not r['cleanup_ok'] or not r['crop_thread_stopped'] for r in summary) else 0


if __name__ == '__main__':
    raise SystemExit(main())
