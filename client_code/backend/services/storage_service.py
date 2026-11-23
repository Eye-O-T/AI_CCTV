"""단일 백엔드의 저장소 설정 파일과 이벤트 쓰기 사이의 경계를 관리한다."""
from datetime import date
import json
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
from threading import RLock

from sqlalchemy import select, text

from ..errors import APIError
from ..models.event import Event
from ..schemas.event import MEDIA_FIELDS, _relative_path
from ..schemas.storage import StorageResponse, StorageUpdate


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _invalid(field='storage_root'):
    return APIError(422, 'VALIDATION_ERROR', '저장소와 완료된 미디어 경로를 확인해 주세요.', field)


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


class StorageService:
    def __init__(self, session_factory, config_path: Path):
        self.session_factory = session_factory
        self.config_path = config_path.resolve()
        self.lock = RLock()
        self.root = None
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding='utf-8'))
                body = StorageUpdate.model_validate(data)
                root = Path(body.storage_root)
                if not root.is_absolute() or root.name.casefold() != 'ai_cctv' or str(root) != body.storage_root:
                    raise ValueError('Invalid stored root')
                # 외장 저장소가 잠시 없어도 조회와 이력 조회는 유지한다. 쓰기는 require_root가 검사한다.
                self.root = root
            except (OSError, ValueError, TypeError):
                raise RuntimeError('저장소 설정 파일을 읽을 수 없습니다. 기존 설정을 확인하세요.') from None

    def _locked(self, db):
        return db.scalar(select(Event.event_id).limit(1)) is not None

    def get(self):
        with self.lock, self.session_factory() as db:
            return StorageResponse(storage_root=str(self.root) if self.root else None, locked=self._locked(db))

    def update(self, body: StorageUpdate):
        with self.lock, self.session_factory() as db:
            # EventService와 같은 잠금 순서. 설정 파일 교체가 끝나기 전에 이벤트가 커밋될 수 없다.
            db.execute(text('BEGIN IMMEDIATE'))
            raw = body.storage_root
            try:
                candidate = Path(raw)
                if (raw != raw.strip() or any(ord(c) < 32 for c in raw)
                        or not candidate.is_absolute() or candidate.name.casefold() != 'ai_cctv'):
                    raise ValueError('Invalid root')
                candidate = candidate.resolve(strict=True)
                if (candidate.name.casefold() != 'ai_cctv' or not candidate.is_dir()
                        or candidate.stat().st_dev != PROJECT_ROOT.stat().st_dev):
                    raise ValueError('Directory on project volume required')
                if any(candidate.is_relative_to(PROJECT_ROOT / part) for part in ('temp', 'output')):
                    raise ValueError('Temporary/analysis storage is not a final root')
            except (OSError, ValueError, RuntimeError):
                raise _invalid() from None
            locked = self._locked(db)
            if locked and candidate != self.root:
                raise APIError(409, 'STORAGE_ROOT_LOCKED', '이벤트 저장 이후 저장소 기준 경로는 변경할 수 없습니다.', 'storage_root')
            response = StorageResponse(storage_root=str(candidate), locked=locked)
            if candidate != self.root:
                self._write(candidate)
                self.root = candidate
            return response

    def _write(self, root):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        pending = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.config_path.parent,
                                    prefix=self.config_path.name + '.', suffix='.pending', delete=False) as handle:
                pending = Path(handle.name)
                json.dump({'storage_root': str(root)}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            # 같은 디렉터리의 임시 파일을 완전히 닫고 원자적으로 교체한다.
            os.replace(pending, self.config_path)
        finally:
            if pending is not None:
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    pass

    def require_root(self):
        if self.root is None:
            raise APIError(409, 'STORAGE_ROOT_NOT_CONFIGURED', '이벤트 저장 전에 저장소를 설정해 주세요.', 'storage_root')
        try:
            if (not self.root.is_dir() or self.root.resolve(strict=True) != self.root or _is_link(self.root)
                    or self.root.stat().st_dev != PROJECT_ROOT.stat().st_dev
                    or any(self.root.is_relative_to(PROJECT_ROOT / part) for part in ('temp', 'output'))):
                raise ValueError('Storage root unavailable')
        except (OSError, ValueError, RuntimeError):
            raise _invalid() from None
        return self.root

    def validate_media(self, values):
        root = self.require_root()
        parents = set()
        for field in MEDIA_FIELDS:
            value = values[field]
            if value is None:
                continue
            try:
                _relative_path(value)
                parts = value.split('/')
                if (len(parts) != 6 or parts[0] != '이벤트'
                        or parts[1] != f"person_{values['global_person_id']}"
                        or parts[3] != f"camera_{values['camera_id']}"
                        or parts[5] != field.removesuffix('_path') + ('.mp4' if field == 'clip_path' else '.jpg')
                        or date.fromisoformat(parts[2]).isoformat() != parts[2]):
                    raise ValueError('Event media layout required')
                path = root
                for part in parts:
                    path = path / part
                    if _is_link(path):
                        raise ValueError('Links/reparse points are not event media')
                if not path.resolve(strict=True).is_relative_to(root) or not path.is_file() or path.stat().st_size == 0:
                    raise ValueError('Completed file required')
                parents.add(path.parent)
            except (OSError, ValueError, RuntimeError):
                raise _invalid(field) from None
        if len(parents) > 1:
            raise _invalid(None)
