"""실제 웹캠/YOLO/OSNet/임시 HTTP 백엔드의 저장·STOP·재시작 진단.

프로젝트 output/storage_stage4_LABEL과 temp/storage_stage4_LABEL만 새로 사용한다.
운영 DB/기존 저장물을 변경하지 않으며 제품 GUI 조작 검증은 아니다.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'client_code'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--indices', nargs='+', type=int, required=True)
    parser.add_argument('--seconds', type=float, default=12)
    parser.add_argument('--label', default=datetime.now().strftime('%Y%m%d_%H%M%S'))
    args = parser.parse_args()
    if (not 1 <= len(args.indices) <= 2 or len(set(args.indices)) != len(args.indices)
            or min(args.indices) < 0 or not 3 <= args.seconds <= 60):
        parser.error('서로 다른 웹캠 1~2대와 3~60초 관측 시간이 필요합니다.')
    if not args.label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.label):
        parser.error('label은 영문/숫자/밑줄/하이픈만 허용합니다.')
    base = ROOT / 'output' / ('storage_stage4_' + args.label)
    base.mkdir(parents=True, exist_ok=False)
    staging = ROOT / 'temp' / ('storage_stage4_' + args.label)
    staging.mkdir(parents=True, exist_ok=False)
    os.chdir(ROOT / 'client_code')  # 기존 YOLO 가중치 상대 경로
    (base / 'ultralytics-config' / 'Ultralytics').mkdir(parents=True)
    os.environ['YOLO_CONFIG_DIR'] = str(base / 'ultralytics-config')

    import cv2
    import numpy as np
    import requests
    import torch
    from PyQt5.QtCore import QCoreApplication
    from reid.pipeline import ReIDPipeline
    import storage.recording_manager as recordings
    import workers.video_worker as videos

    recordings.DEFAULT_TEMP_ROOT = staging
    videos.DEFAULT_MEDIA_TEMP_ROOT = staging
    app = QCoreApplication.instance() or QCoreApplication([])
    client = requests.Session()
    client.trust_env = False
    workers, records = [], []
    pipeline = backend = None
    backend_log = (base / 'backend.log').open('w', encoding='utf-8')
    db = ROOT / 'client_code/backend/data/ai_cctv.db'
    before = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None
    report = dict(indices=args.indices, seconds_per_phase=args.seconds, failure=None,
                  cuda_available=torch.cuda.is_available(), runs=[], files=[])

    def pump_until(predicate, timeout):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise TimeoutError('Probe phase timeout')
            time.sleep(.02)
        app.processEvents()

    def observe(seconds):
        until = time.monotonic() + seconds
        pump_until(lambda: time.monotonic() >= until, seconds + 2)

    def launch(camera_id, index, phase):
        worker = videos.VideoWorker(camera_id=camera_id, source=index, reid_pipeline=pipeline,
                                   ai_cctv_path=str(base / 'selected/AI_CCTV'),
                                   original_segment_seconds=10, clip_max_seconds=10)
        worker.crop_worker.collector.output_root = base / 'osnet-crops'
        record = dict(camera_id=camera_id, webcam_index=index, phase=phase, frames=0,
                      max_persons=0, global_ids=[], errors=0, error_categories=[], media={}, frame_times=[])
        workers.append(worker)
        records.append(record)

        def frame(_):
            record['frames'] += 1
            record['frame_times'].append(time.monotonic())

        def media(update):
            key = (update.tracking_generation, update.local_person_id)
            record['media'][key] = update

        def event(value):
            if value.get('type') == 'error':
                record['errors'] += 1
                message = value.get('message', '')
                categories = ('웹캠 프레임 수신 실패', '영상 스트림 열기 실패', 'YOLO 초기화 실패',
                              '영상 저장 실패', '영상 처리 중 오류', '대표 crop 임시 저장 실패')
                record['error_categories'].append(next((c for c in categories if message.startswith(c)), 'other'))

        worker.frame_ready.connect(frame)
        worker.media_ready.connect(media)
        worker.metrics_ready.connect(lambda m: record.update(max_persons=max(record['max_persons'], m['current_objects'])))
        worker.reid_ready.connect(lambda r: record['global_ids'].append(r.global_person_id))
        worker.event_ready.connect(event)
        worker.start()
        return worker

    def first_frames():
        if any(not worker.isRunning() and not record['frames'] for worker, record in zip(workers, records)):
            raise RuntimeError('VideoWorker ended before first frame')
        return all(r['frames'] for r in records)

    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        backend = subprocess.Popen(
            [sys.executable, '-B', '-m', 'client_code.backend.desktop_server',
             '--db-path', str(base / 'backend.db'), '--port', str(port)], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=backend_log, stderr=backend_log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        url = f'http://127.0.0.1:{port}'

        def healthy():
            if backend.poll() is not None:
                raise RuntimeError('Diagnostic backend exited')
            try:
                return client.get(url + '/health', timeout=.5).status_code == 200
            except requests.RequestException:
                return False

        pump_until(healthy, 15)
        password = secrets.token_urlsafe(24)
        response = client.post(url + '/api/v1/auth/signup', json={
            'username': 'storage-probe', 'password': password, 'password_confirm': password}, timeout=5)
        response.raise_for_status()
        response = client.post(url + '/api/v1/auth/login', json={
            'username': 'storage-probe', 'password': password}, timeout=5)
        response.raise_for_status()
        token = response.json()['access_token']
        pipeline = ReIDPipeline(url, token)
        token = password = None
        for camera_id, index in enumerate(args.indices, 1):
            launch(camera_id, index, 'initial')
        pump_until(first_frames, 35)
        print('PHASE initial observation', flush=True)
        observe(args.seconds)
        other_before = records[1]['frames'] if len(args.indices) > 1 else None
        workers[0].request_stop()
        pump_until(lambda: not workers[0].isRunning(), 25)
        if len(args.indices) > 1:
            pump_until(lambda: records[1]['frames'] >= other_before + 5, 10)
        launch(1, args.indices[0], 'restart')
        pump_until(first_frames, 25)
        print('PHASE restart observation', flush=True)
        observe(args.seconds)
        report['other_camera_continued'] = records[1]['frames'] > other_before if other_before is not None else None
    except Exception as error:
        report['failure'] = type(error).__name__  # 입력/토큰/내부 URL은 출력하지 않는다.
    finally:
        for worker in workers:
            worker.request_stop()
        try:
            pump_until(lambda: all(not w.isRunning() for w in workers), 30)
        except TimeoutError:
            report['failure'] = 'Worker cleanup timeout'
        if pipeline is not None:
            report['osnet_device'] = str(pipeline._extractor.device) if pipeline._extractor else None
            pipeline.request_stop()
            pipeline.join(10)
            report['pipeline_stopped'] = not pipeline.is_alive()
            report['pipeline_failed'] = pipeline.failed
        if backend is not None and backend.poll() is None:
            try:
                backend.stdin.write(b'stop\n')
                backend.stdin.flush()
                backend.communicate(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                backend.kill()  # 이 진단이 생성한 프로세스만 종료한다.
                backend.communicate(timeout=5)
                report['failure'] = 'Diagnostic backend stop failed'
        backend_log.close()
        report['backend_exit_code'] = backend.poll() if backend is not None else None
        client.close()

    for worker, record in zip(workers, records):
        times = record.pop('frame_times')
        record['average_frame_gap_ms'] = round((times[-1] - times[0]) * 1000 / (len(times) - 1), 2) if len(times) > 1 else None
        record['cleanup_ok'] = worker.cleanup_ok
        record['crop_stopped'] = not worker.crop_worker.is_alive()
        record['media'] = [dict(local_id=u.local_person_id, generation=u.tracking_generation,
                                global_id=u.global_person_id, ended=u.finished_at is not None,
                                move_failed=u.move_failed,
                                paths={name: str(path.relative_to(ROOT)) if path else None for name, path in
                                       [('clip', u.clip_path), ('crop', u.crop_path), ('trajectory', u.trajectory_path)]})
                           for u in record['media'].values()]
        report['runs'].append(record)
    for directory in (base / 'selected', base / 'osnet-crops', staging):
        for path in directory.rglob('*'):
            if path.suffix.lower() not in ('.mp4', '.jpg'):
                continue
            item = dict(path=str(path.relative_to(ROOT)), bytes=path.stat().st_size)
            if path.suffix.lower() == '.mp4':
                capture = cv2.VideoCapture(str(path))
                count = 0
                while capture.read()[0]:
                    count += 1
                fps = capture.get(cv2.CAP_PROP_FPS)
                capture.release()
                item['decoded_frames'] = count
                item['fps'] = fps
                item['playback_seconds'] = round(count / fps, 3) if fps > 0 else None
            else:
                item['decoded'] = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR) is not None
            report['files'].append(item)
    report['operational_db_unchanged'] = before == (hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None)
    try:
        with sqlite3.connect((base / 'backend.db').as_uri() + '?mode=ro', uri=True) as connection:
            report['diagnostic_global_persons'] = connection.execute('select count(*) from global_person_manage').fetchone()[0]
    except sqlite3.Error:
        report['diagnostic_global_persons'] = None
    report['saved_event_observations'] = sum(
        u['global_id'] is not None and u['ended'] and not u['move_failed']
        and all(u['paths'].values()) for r in report['runs'] for u in r['media'])
    report['restart_reuses_initial_global_id'] = (bool(set(records[0]['global_ids']) & set(records[-1]['global_ids']))
                                                if len(records) == len(args.indices) + 1 else False)
    report['user_partial_files'] = [str(p.relative_to(ROOT)) for p in (base / 'selected').rglob('*')
                                    if p.is_file() and (p.suffix == '.pending' or p.name == 'recording.mp4')]
    report['passed'] = (report['failure'] is None and report['operational_db_unchanged']
                        and report.get('pipeline_stopped') and not report.get('pipeline_failed')
                        and len(records) == len(args.indices) + 1
                        and all(r['frames'] and r['cleanup_ok'] and not r['errors'] for r in report['runs'])
                        and all(r['global_ids'] for r in report['runs'])
                        and report['restart_reuses_initial_global_id']
                        and report['saved_event_observations'] >= len(records)
                        and all(any(u['global_id'] is not None and u['ended'] and not u['move_failed']
                                    and all(u['paths'].values()) for u in r['media']) for r in report['runs'])
                        and not report['user_partial_files'] and report['backend_exit_code'] == 0
                        and all(f.get('decoded', f.get('decoded_frames', 0) > 0) for f in report['files']))
    (base / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('passed', 'failure', 'saved_event_observations',
                                          'operational_db_unchanged', 'diagnostic_global_persons')}, ensure_ascii=False))
    print('REPORT', base / 'report.json')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
