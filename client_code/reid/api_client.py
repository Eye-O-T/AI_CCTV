"""Manager 작업 스레드용 동기 HTTP. GUI 인증 객체를 직접 호출하지 않는다."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from threading import Lock
from urllib.parse import urlsplit

import requests

from .tasks import decode_vector, encode_vector


class GlobalPersonApiError(RuntimeError):
    def __init__(self, code, retryable=False):
        self.code = code
        self.retryable = retryable
        super().__init__('글로벌 인물 API 처리를 확인하세요. (' + code + ')')


@dataclass(frozen=True)
class GlobalPersonRecord:
    global_person_id: int
    feature_vector: tuple[float, ...] = field(repr=False)
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime


def parse_record(payload):
    try:
        if not isinstance(payload, dict) or set(payload) != {
            'global_person_id', 'feature_vector', 'created_at', 'updated_at', 'last_seen_at'
        }:
            raise ValueError
        person_id = payload['global_person_id']
        if type(person_id) is not int or person_id < 1:
            raise ValueError
        vector = tuple(decode_vector(encode_vector(payload['feature_vector'])))
        dates = []
        for field_name in ('created_at', 'updated_at', 'last_seen_at'):
            value = payload[field_name]
            if not isinstance(value, str):
                raise ValueError
            value = datetime.fromisoformat(value)
            if value.utcoffset() is None:
                raise ValueError
            dates.append(value.astimezone(timezone.utc))
        return GlobalPersonRecord(person_id, vector, *dates)
    except (ValueError, TypeError, OverflowError, KeyError):
        raise GlobalPersonApiError('INVALID_RESPONSE', retryable=True) from None


def _utc_string(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class GlobalPersonApiClient:
    def __init__(self, base_url, access_token, timeout=5):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('백엔드 기본 주소가 필요합니다.')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('양수 제한 시간이 필요합니다.')
        self.base_url = base_url.rstrip('/') + '/api/v1/global-persons'
        self.timeout = timeout
        self._token_lock = Lock()
        self._access_token = access_token
        self._http = requests.Session()
        self._http.trust_env = False

    def set_access_token(self, access_token):
        """같은 로그인 실행의 갱신 토큰을 caller가 전달한다. 자동 로그인/갱신은 하지 않는다."""
        with self._token_lock:
            self._access_token = access_token

    def close(self):
        self.set_access_token(None)
        self._http.close()

    def _request(self, method, path='', payload=None):
        with self._token_lock:
            token = self._access_token
        if not isinstance(token, str) or not token:
            raise GlobalPersonApiError('INVALID_ACCESS_TOKEN')
        try:
            with self._http.request(method, self.base_url + path, json=payload,
                                    headers={'Authorization': 'Bearer ' + token},
                                    timeout=self.timeout, allow_redirects=False) as response:
                if response.status_code == 401:
                    raise GlobalPersonApiError('INVALID_ACCESS_TOKEN')
                if response.status_code >= 500:
                    raise GlobalPersonApiError('SERVER_ERROR', retryable=True)
                expected = {'GET': (200,), 'POST': (200, 201), 'PATCH': (200,)}[method]
                if response.status_code not in expected:
                    code = {404: 'GLOBAL_PERSON_NOT_FOUND', 409: 'GLOBAL_PERSON_REQUEST_CONFLICT',
                            422: 'VALIDATION_ERROR'}.get(response.status_code, 'UNEXPECTED_HTTP_STATUS')
                    raise GlobalPersonApiError(code)
                return response.json()
        except requests.exceptions.JSONDecodeError:
            raise GlobalPersonApiError('INVALID_RESPONSE', retryable=True) from None
        except requests.RequestException:
            raise GlobalPersonApiError('CONNECTION_ERROR', retryable=True) from None
        except ValueError:
            raise GlobalPersonApiError('INVALID_RESPONSE', retryable=True) from None

    def list_people(self):
        payload = self._request('GET')
        if (not isinstance(payload, dict) or type(payload.get('feature_dimension')) is not int
                or payload['feature_dimension'] != 512 or not isinstance(payload.get('items'), list)):
            raise GlobalPersonApiError('INVALID_RESPONSE', retryable=True)
        records = [parse_record(item) for item in payload['items']]
        if len({row.global_person_id for row in records}) != len(records):
            raise GlobalPersonApiError('INVALID_RESPONSE', retryable=True)
        return records

    def create_person(self, request_id, vector, observed_at):
        payload = {'request_id': str(request_id), 'feature_vector': list(vector),
                   'observed_at': _utc_string(observed_at)}
        return parse_record(self._request('POST', payload=payload))

    def update_last_seen(self, person_id, observed_at):
        return parse_record(self._request('PATCH', f'/{person_id}/last-seen',
                                         {'observed_at': _utc_string(observed_at)}))
