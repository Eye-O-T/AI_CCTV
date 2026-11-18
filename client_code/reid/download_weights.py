"""저자 배포 가중치 취득. 추론 중 자동 다운로드는 하지 않는다."""
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

from .config import DEFAULT_WEIGHTS_PATH, WEIGHTS_SHA256, WEIGHTS_SIZE, WEIGHTS_URL


def weights_match(path):
    path = Path(path)
    if path.stat().st_size != WEIGHTS_SIZE:
        return False
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest() == WEIGHTS_SHA256


def download_weights(destination=DEFAULT_WEIGHTS_PATH):
    destination = Path(destination).resolve()
    if destination.exists():
        if weights_match(destination):
            return destination
        raise RuntimeError('기존 가중치가 기준 해시와 다릅니다. 보존 후 별도로 확인하세요.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.download', delete=False) as handle:
            temporary = Path(handle.name)
            request = Request(WEIGHTS_URL, headers={'User-Agent': 'AI-CCTV-ReID'})
            with urlopen(request, timeout=60) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > WEIGHTS_SIZE:
                        raise RuntimeError('가중치 파일 크기가 기준과 다릅니다.')
                    handle.write(chunk)
        if not weights_match(temporary):
            raise RuntimeError('다운로드한 가중치의 크기 또는 SHA-256이 일치하지 않습니다.')
        # 기존 파일이 중간에 생기면 덮어쓰지 않는다.
        os.link(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    download_weights()
    print(f'OSNet 가중치 준비 완료: SHA-256 {WEIGHTS_SHA256}')
