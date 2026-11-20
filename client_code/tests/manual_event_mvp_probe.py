"""이벤트 MVP 종합 진단. 운영 계정/DB/저장소를 사용하지 않는다.

synthetic: 제품 GUI/스레드/crop/API/파일 전체, 영상·탐지·특징 벡터만 대역.
webcam: 임시 계정으로 제품 창을 열고 사용자가 START/STOP/로그아웃한다.
새 진단 파일은 output/event_mvp_stage5_LABEL, temp/event_mvp_stage5_LABEL,
verification_media/event_mvp_stage5_LABEL/AI_CCTV에 보존한다.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def fingerprint():
    directory = ROOT / 'client_code/backend/data'
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() if (directory / name).is_file() else None
            for name in ('ai_cctv.db', 'ai_cctv.db-wal', 'ai_cctv.db-shm', 'ai_cctv.storage.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('synthetic', 'webcam'), required=True)
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--label', default=datetime.now().strftime('%Y%m%d_%H%M%S'))
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--auto-finish-after-disappearance', action='store_true',
                        help='webcam에서 자연 사라짐 저장 확인 후 진단 도구가 STOP/로그아웃한다.')
    args = parser.parse_args()
    if args.index < 0 or not 30 <= args.timeout <= 600 or not args.label or any(
            c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.label):
        parser.error('장치 번호·30~600초 제한 시간·영문/숫자/밑줄/하이픈 label을 확인하세요.')
    name = 'event_mvp_stage5_' + args.label
    base, staging = ROOT / 'output' / name, ROOT / 'temp' / name
    selected = ROOT / 'verification_media' / name / 'AI_CCTV'
    for path in (base, staging, selected):
        if path.exists():
            parser.error('기존 진단 경로가 있습니다. 새 label을 사용하세요.')
    for path in (base, staging, selected):
        path.mkdir(parents=True, exist_ok=False)
    (base / 'ultralytics-config').mkdir()
    os.environ['YOLO_CONFIG_DIR'] = str(base / 'ultralytics-config')
    if args.mode == 'synthetic':
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    else:
        os.environ.pop('QT_QPA_PLATFORM', None)
    sys.path[:0] = [str(ROOT / 'client_code'), str(ROOT)]
    os.chdir(ROOT / 'client_code')

    import cv2
    import numpy as np
    import requests
    from PyQt5 import sip
    from PyQt5.QtCore import QProcess
    from PyQt5.QtWidgets import QApplication, QLabel
    from app_controller import AppController
    from auth.api_client import AuthApiClient
    from auth.backend_process import BackendProcess
    from auth.session_manager import SessionManager
    from reid.pipeline import ReIDPipeline
    from ui.gui import CCTVMainWindow
    from workers.crop_worker import CropWorker
    import workers.video_worker as videos
    import storage.recording_manager as recordings

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    originals = fingerprint()
    report = dict(mode=args.mode, index=args.index if args.mode == 'webcam' else None,
                  started_at=datetime.now(timezone.utc).isoformat(), failure=None, runs=[], files=[], checks={})
    workers, snapshots = [], {}
    before_crop_hashes = {}
    present = True
    frames = np.random.default_rng(19).integers(0, 256, (240, 320, 3), dtype=np.uint8)
    video_class = videos.VideoWorker
    window = None
    guide = None
    coordinator = None
    controller = None
    session = None
    observer = requests.Session()
    observer.trust_env = False
    api_rows = []

    def pump_until(predicate, timeout=20):
        until = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() >= until:
                raise TimeoutError('Diagnostic phase timed out')
            time.sleep(.01)
        app.processEvents()

    def write_status(phase):
        public = dict(phase=phase, mode=args.mode,
                      runs=[{k: v for k, v in r.items() if k != 'errors'} for r in report['runs']],
                      event_pending=len(coordinator._pending) if coordinator else None,
                      save_failures=coordinator.failures if coordinator else None)
        public['saved_crop_images'] = len(list((base / 'osnet-crops').rglob('crop_*.jpg')))
        pending = base / 'status.pending'
        pending.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(pending, base / 'status.json')

    class SyntheticStream:
        is_rtsp = False
        def open(self): return True
        def get_fps(self): return 24
        def read(self):
            time.sleep(1 / 24)
            return True, frames.copy()
        def release(self): return True
        def pop_connection_events(self): return []

    class SyntheticTracker:
        tracking_generation = 0
        def __init__(self, **_): pass
        def track(self, _):
            return [dict(person_id=1, bbox=(20, 20, 150, 190), conf=.99)] if present else []
        def expire_persons(self, _): pass

    class SyntheticExtractor:
        def extract(self, paths):
            if len(paths) != 5 or not all(Path(p).is_file() for p in paths):
                raise ValueError('Five real crop files required')
            return [1.] + [0.] * 511

    def make_video(**kwargs):
        worker = video_class(**kwargs)
        worker.crop_worker = CropWorker(kwargs['camera_id'], output_root=base / 'osnet-crops')
        if args.mode == 'synthetic':
            worker.stream = SyntheticStream()
        workers.append(worker)
        return worker

    def attach(camera):
        run = window.runtime.runs[camera['camera_id']]
        worker = run.worker
        record = dict(execution_id=worker.execution_id, frames=0, frame_shapes=[], global_ids=[], errors=[],
                      cleanup_ok=False, finished=False, first_frame_at=None, cleanup_finished_at=None,
                      current_objects=0, tracked_total=0)
        report['runs'].append(record)
        def frame(image):
            record['frames'] += 1
            shape = list(image.shape[:2])
            if shape not in record['frame_shapes']:
                record['frame_shapes'].append(shape)
        def media(update):
            key = (update.execution_id, update.tracking_generation, update.local_person_id)
            snapshots[key] = update
        def reid(result):
            if result.global_person_id not in record['global_ids']:
                record['global_ids'].append(result.global_person_id)
            for path in (base / 'osnet-crops').rglob('crop_*.jpg'):
                before_crop_hashes.setdefault(str(path), hashlib.sha256(path.read_bytes()).hexdigest())
        def done():
            record.update(cleanup_ok=worker.cleanup_ok, finished=True,
                          first_frame_at=worker.first_frame_at.isoformat() if worker.first_frame_at else None,
                          cleanup_finished_at=worker.cleanup_finished_at.isoformat() if worker.cleanup_finished_at else None)
        worker.frame_ready.connect(frame)
        worker.metrics_ready.connect(lambda metrics: record.update(
            current_objects=metrics.get('current_objects', 0), tracked_total=metrics.get('tracked_total', 0)))
        worker.media_ready.connect(media)
        worker.reid_ready.connect(reid)
        worker.event_ready.connect(lambda value: record['errors'].append(value.get('type'))
                                  if value.get('type') == 'error' else None)
        worker.finished.connect(done)

    def make_window():
        nonlocal window, guide
        window = CCTVMainWindow()
        window.setWindowTitle('이벤트 MVP 5단계 · 임시 DB · ' + args.mode)
        guide = QLabel('검증용 임시 저장소입니다. START → Global ID 확인 → 화면 밖에서 기다리세요.')
        guide.setWordWrap(True)
        guide.setStyleSheet('font-size: 20px; padding: 12px; color: #facc15;')
        window.centralWidget().layout().insertWidget(0, guide)
        window.runtime.started.connect(attach)
        if args.mode == 'synthetic':
            window.runtime.reid_factory = lambda url, token: ReIDPipeline(url, token, extractor_factory=SyntheticExtractor)
        return window

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    username, password = 'eventprobe', secrets.token_urlsafe(24)
    stack = ExitStack()
    try:
        stack.enter_context(patch.object(videos, 'VideoWorker', side_effect=make_video))
        stack.enter_context(patch.object(videos, 'DEFAULT_MEDIA_TEMP_ROOT', staging))
        stack.enter_context(patch.object(recordings, 'DEFAULT_TEMP_ROOT', staging))
        if args.mode == 'synthetic':
            stack.enter_context(patch.object(videos, 'PersonTracker', SyntheticTracker))
        backend = BackendProcess(db_path=base / 'backend.db', port=port)
        session = SessionManager(AuthApiClient(url))
        controller = AppController(session=session, backend=backend, main_factory=make_window)
        controller.start()
        pump_until(lambda: controller.auth_window.mode in ('signup', 'error'), 35)
        if controller.auth_window.mode != 'signup':
            raise RuntimeError('Diagnostic backend startup failed')
        session.signup(username, password, password)
        pump_until(lambda: not session.busy and controller.auth_window.mode == 'login')
        session.login(username, password)
        pump_until(lambda: controller.main_window is not None, 15)
        product_token = session.access_token
        coordinator = window.runtime.event_store
        pump_until(lambda: coordinator.loaded)
        coordinator.set_root(str(selected))
        pump_until(lambda: coordinator.ready)
        # 관찰용 별도 로그인. 제품 세션 로그아웃 후 이력 조회·토큰 폐기 확인에만 사용한다.
        response = observer.post(url + '/api/v1/auth/login', json={'username': username, 'password': password}, timeout=5)
        response.raise_for_status()
        observer_tokens = response.json()
        observer.headers['Authorization'] = 'Bearer ' + observer_tokens['access_token']
        response = observer.post(url + '/api/v1/cameras', json={
            'name': 'stage5', 'camera_type': 'webcam', 'webcam_index': args.index}, timeout=5)
        response.raise_for_status()
        camera_id = response.json()['camera_id']
        window.camera_saved(camera_id)
        pump_until(lambda: camera_id in window.camera_rows and window.camera_api.pending is None)
        window.camera_list.setCurrentRow(1)
        password = None
        write_status('ready_no_camera_opened')
        print('READY', str(base / 'status.json'), flush=True)

        if args.mode == 'synthetic':
            for phase in ('natural', 'stop_present'):
                present = True
                window.start_video()
                pump_until(lambda: bool(window.runtime.runs) and window.runtime.runs[camera_id].worker is not None)
                run = window.runtime.runs[camera_id]
                worker = run.worker
                pump_until(lambda: bool(worker.global_person_ids), 20)
                if phase == 'natural':
                    present = False
                    pump_until(lambda: not worker.state_manager.person_states, 8)
                    pump_until(lambda: not coordinator._persons and not coordinator._pending)
                write_status(phase)
                window.stop_video()
                pump_until(lambda: not window.runtime.runs, 20)
                pump_until(lambda: not coordinator._pending and not coordinator._persons)
        else:
            until = time.monotonic() + args.timeout
            next_status = 0
            while controller.main_window is not None:
                app.processEvents()
                if time.monotonic() > until:
                    raise TimeoutError('Manual capture timeout')
                if time.monotonic() >= next_status:
                    matched = any(record['global_ids'] for record in report['runs'])
                    natural_saved = any(update.finish_reason == 'natural' and
                        (update.camera_id, update.execution_id.replace('-', ''), update.tracking_generation,
                         update.local_person_id) in coordinator._closed_persons for update in snapshots.values())
                    if natural_saved and not coordinator.failures and not coordinator._pending:
                        guide.setText('사라짐 이벤트 저장 확인 완료. 이제 STOP → 로그아웃을 눌러 주세요.')
                        guide.setStyleSheet('font-size: 20px; padding: 12px; color: #4ade80;')
                        if args.auto_finish_after_disappearance:
                            write_status('natural_disappearance_confirmed')
                            window.stop_video()
                            pump_until(lambda: not window.runtime.runs and not coordinator._pending, 20)
                            controller.request_logout()
                            pump_until(lambda: controller.main_window is None and session.access_token is None, 35)
                            break
                    elif matched:
                        guide.setText('Global ID 확인 완료. 화면 밖으로 완전히 나가 기다려 주세요. 아직 STOP을 누르지 마세요.')
                    else:
                        guide.setText('START 후 전신이 보이도록 서서 Global ID가 표시될 때까지 기다려 주세요.')
                    write_status('webcam_user_control')
                    next_status = time.monotonic() + 1
                time.sleep(.01)
        old_token = product_token
        if controller.main_window is not None:
            controller.request_logout()
            pump_until(lambda: controller.main_window is None and session.access_token is None, 35)
        report['event_queue_drained'] = coordinator.finished and not coordinator.failures
        if old_token:
            response = observer.get(url + '/api/v1/events', headers={'Authorization': 'Bearer ' + old_token}, timeout=5)
            report['product_token_revoked'] = response.status_code == 401
        response = observer.get(url + '/api/v1/events', params={'limit': 100}, timeout=5)
        response.raise_for_status()
        api_rows = response.json()['items']
        report['api_events'] = api_rows
        observer.post(url + '/api/v1/auth/logout', json={'refresh_token': observer_tokens['refresh_token']}, timeout=5)
        controller.request_exit()
        pump_until(lambda: backend.process.state() == QProcess.NotRunning, 20)
        report['backend_exit_code'] = backend.process.exitCode()
    except Exception as error:
        report['failure'] = type(error).__name__  # 원본 요청/토큰/장치 주소를 기록하지 않는다.
    finally:
        for worker in workers:
            if not sip.isdeleted(worker) and worker.isRunning():
                worker.request_stop()
        if controller is not None and backend.process.state() != QProcess.NotRunning:
            controller.request_exit()
            try:
                pump_until(lambda: backend.process.state() == QProcess.NotRunning, 35)
            except TimeoutError:
                report['failure'] = report['failure'] or 'CleanupTimeout'
                backend.process.kill()  # 이 진단이 만든 백엔드만 종료한다.
                backend.process.waitForFinished(5000)
        if coordinator is not None:
            coordinator.store.abort()
            coordinator.store.join(5)
        for worker in workers:
            if not sip.isdeleted(worker) and worker.isRunning():
                worker.wait(5000)
        observer.close()
        stack.close()

    # 서버 종료 후 진단 DB만 읽는다. 운영 DB는 해시 비교만 한다.
    try:
        with closing(sqlite3.connect((base / 'backend.db').as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute('SELECT * FROM events ORDER BY occurred_at DESC,event_id DESC')]
            report['diagnostic_global_persons'] = db.execute('SELECT count(*) FROM global_person_manage').fetchone()[0]
        report['db_events'] = rows
    except sqlite3.Error:
        rows = []
        report['failure'] = report['failure'] or 'DiagnosticDatabaseReadFailed'
    counts = Counter(row['event_type'] for row in rows)
    report['event_counts'] = dict(counts)
    report['operational_files_unchanged'] = originals == fingerprint()
    checks = report['checks']
    checks['operational_files_unchanged'] = report['operational_files_unchanged']
    checks['camera_events'] = bool(report['runs']) and counts['camera_started'] == counts['camera_stopped'] == len(report['runs'])
    checks['observed_people'] = counts['person_appeared'] >= 1 and counts['person_disappeared'] >= 1
    checks['api_db_agree'] = bool(api_rows) and {r['event_id'] for r in rows} == {r['event_id'] for r in api_rows}
    checks['cleanup'] = bool(report['runs']) and all(r['cleanup_ok'] and r['finished'] and r['frames'] and not r['errors'] for r in report['runs'])
    checks['event_queue_drained'] = report.get('event_queue_drained', False)
    checks['product_token_revoked'] = report.get('product_token_revoked', False)
    checks['paths_and_timestamps'] = True
    appearances = {r['event_id']: r for r in rows if r['event_type'] == 'person_appeared'}
    keys = set()
    for row in rows:
        key = tuple(row[f] for f in ('server_run_id', 'camera_id', 'camera_execution_id', 'tracking_generation', 'local_person_id', 'event_type'))
        if key in keys:
            checks['paths_and_timestamps'] = False
        keys.add(key)
        if row['event_type'] == 'person_appeared':
            key = (row['camera_execution_id'].replace('-', ''), row['tracking_generation'], row['local_person_id'])
            update = snapshots.get(key)
            if update is None or datetime.fromisoformat(row['occurred_at']) != update.first_seen_utc:
                checks['paths_and_timestamps'] = False
            for field in ('crop_path', 'clip_path', 'trajectory_path'):
                path = selected / row[field] if row[field] else None
                if path is None or not path.is_file() or '~' not in path.parent.name:
                    checks['paths_and_timestamps'] = False
        elif row['event_type'] == 'person_disappeared':
            appeared = appearances.get(row['related_event_id'])
            if appeared is None or any(row[f] != appeared[f] for f in
                    ('camera_id', 'camera_execution_id', 'tracking_generation', 'local_person_id', 'global_person_id')):
                checks['paths_and_timestamps'] = False
            update = snapshots.get((row['camera_execution_id'].replace('-', ''), row['tracking_generation'], row['local_person_id']))
            if update is None or update.finish_reason != 'natural' or datetime.fromisoformat(row['occurred_at']) != update.last_seen_utc:
                checks['paths_and_timestamps'] = False
    report['crop_hashes_preserved'] = bool(before_crop_hashes) and all(Path(p).is_file() and
        hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in before_crop_hashes.items())
    checks['crop_originals'] = report['crop_hashes_preserved'] and len(before_crop_hashes) >= counts['person_appeared'] * 5
    for directory in (selected, base / 'osnet-crops', staging):
        for path in directory.rglob('*'):
            if path.suffix not in ('.jpg', '.mp4'):
                continue
            item = dict(path=str(path.relative_to(ROOT)), size=path.stat().st_size)
            if path.suffix == '.mp4':
                capture = cv2.VideoCapture(str(path))
                decoded = 0
                while capture.read()[0]:
                    decoded += 1
                capture.release()
                item['decoded_frames'] = decoded
            else:
                item['decoded'] = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR) is not None
            report['files'].append(item)
    checks['media_decode'] = bool(report['files']) and all(f.get('decoded', f.get('decoded_frames', 0) > 0) for f in report['files'])
    checks['no_pending_user_files'] = not any(p.is_file() and (p.suffix == '.pending' or p.name == 'recording.mp4') for p in selected.rglob('*'))
    if args.mode == 'synthetic':
        checks['exact_synthetic_counts'] = dict(counts) == dict(camera_started=2, camera_stopped=2, person_appeared=2, person_disappeared=1)
        checks['restart_global_id'] = report.get('diagnostic_global_persons') == 1 and all(r['global_ids'] == [1] for r in report['runs'])
    report['passed'] = report['failure'] is None and all(checks.values()) and report.get('backend_exit_code') == 0
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (base / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], failure=report['failure'], event_counts=report['event_counts'],
                         checks=checks, report=str(base / 'report.json')), ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
