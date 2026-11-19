"""영상/GUI를 대기시키지 않는 단일 HTTP 작업 스레드."""
from copy import deepcopy
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time
from uuid import uuid4

import requests


class EventStore(Thread):
    def __init__(self, base_url, access_token, capacity=128):
        super().__init__(name='event-store', daemon=True)
        self.base_url = base_url.rstrip('/')
        self._token = access_token
        self._lock = Lock()
        self._jobs = Queue(capacity)
        self._results = Queue(capacity * 2)
        self._abort = Event()
        self._closing = False
        self._deadline = float('inf')

    def set_access_token(self, token):
        with self._lock:
            self._token = token

    def submit(self, method, path, body=None, kind='', camera_id=None):
        with self._lock:
            if self._closing or self._abort.is_set():
                return None
            job = dict(request_id=uuid4().hex, method=method, path=path,
                       body=deepcopy(body), kind=kind, camera_id=camera_id)
            try:
                self._jobs.put_nowait(job)
            except Full:
                return None
            return job['request_id']

    def take_results(self):
        results = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except Empty:
                return results

    def finish(self, timeout=8):
        with self._lock:
            self._closing = True
            self._deadline = min(self._deadline, time.monotonic() + timeout)

    def abort(self):
        with self._lock:
            self._closing = True
            self._token = None
        self._abort.set()

    def _request(self, session, job):
        # 응답 유실·일시 오류만 같은 본문으로 한 번 재시도한다. 401도 최신 토큰이 있을 때만 재시도한다.
        for attempt in range(2):
            if self._abort.is_set() or time.monotonic() >= self._deadline:
                return None, 'SAVE_CANCELLED'
            with self._lock:
                token = self._token
            try:
                with session.request(job['method'], self.base_url + job['path'], json=job['body'],
                                     headers={'Authorization': 'Bearer ' + (token or '')},
                                     timeout=(1, 2), allow_redirects=False, stream=True) as response:
                    status = response.status_code
                    if status == 401:
                        with self._lock:
                            refreshed = self._token != token
                        if refreshed and attempt == 0:
                            continue
                        return None, 'INVALID_ACCESS_TOKEN'
                    if status >= 500:
                        if attempt == 0:
                            continue
                        return None, 'SERVER_ERROR'
                    if status not in (200, 201):
                        return None, 'REQUEST_REJECTED'
                    raw = bytearray()
                    for chunk in response.iter_content(16384):
                        if self._abort.is_set() or time.monotonic() >= self._deadline or len(raw) + len(chunk) > 65536:
                            return None, 'SAVE_CANCELLED'
                        raw.extend(chunk)
                    import json
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError('Invalid JSON object')
                    return payload, ''
            except (requests.RequestException, ValueError):
                if attempt:
                    return None, 'CONNECTION_ERROR'
        return None, 'CONNECTION_ERROR'

    def run(self):
        with requests.Session() as session:
            session.trust_env = False
            while not self._abort.is_set():
                if time.monotonic() >= self._deadline:
                    break
                try:
                    job = self._jobs.get(timeout=.05)
                except Empty:
                    if self._closing:
                        break
                    continue
                try:
                    payload, error = self._request(session, job)
                except Exception:
                    payload, error = None, 'STORE_FAILED'
                result = {key: job[key] for key in ('request_id', 'kind', 'camera_id')}
                result.update(payload=payload, error=error)
                while not self._abort.is_set():
                    try:
                        self._results.put(result, timeout=.05)
                        break
                    except Full:
                        if time.monotonic() >= self._deadline:
                            break
        with self._lock:
            self._token = None
