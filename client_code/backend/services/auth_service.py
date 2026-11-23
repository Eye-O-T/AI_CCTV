"""계정 트랜잭션과 단일 프로세스 로그인 세션의 수명 관리."""

from dataclasses import dataclass
import secrets
from threading import RLock
import time

import jwt
from sqlalchemy import text

from ..errors import APIError
from ..repositories.user_repository import UserRepository
from ..schemas.auth import LoginResponse, SignupStatusResponse, TokenResponse, UserResponse
from ..security import TokenSigner, hash_password, refresh_digest, verify_password


ACCESS_SECONDS = 900
SESSION_SECONDS = 28800


@dataclass(frozen=True)
class LoginSession:
    user_id: int
    run_id: int
    expires_at: int
    refresh_hash: str


class AuthService:
    def __init__(self, session_factory, run_id: int, clock=time.time):
        self.session_factory = session_factory
        self.run_id = run_id
        self.clock = clock
        self.repository = UserRepository()
        self.signer = TokenSigner()
        self._sessions: dict[str, LoginSession] = {}
        self._refresh_index: dict[str, str] = {}
        self._lock = RLock()
        self._closed = False
        # 없는 아이디도 비밀번호 검증 비용을 거치게 한다.
        self._dummy_hash = hash_password(secrets.token_urlsafe(32))

    def signup_status(self) -> SignupStatusResponse:
        with self.session_factory() as db:
            return SignupStatusResponse(signup_required=not self.repository.exists(db))

    def signup(self, username: str, password: str, confirmation: str) -> UserResponse:
        if password != confirmation:
            raise APIError(422, "PASSWORD_CONFIRM_MISMATCH", "비밀번호와 비밀번호 확인이 일치하지 않습니다.", "password_confirm")
        with self.session_factory() as db:
            try:
                # SELECT 이전에 SQLite 쓰기 예약: 서로 다른 아이디의 동시 최초 가입도 직렬화.
                db.execute(text("BEGIN IMMEDIATE"))
                if self.repository.exists(db):
                    raise APIError(409, "SIGNUP_CLOSED", "관리자 계정이 이미 존재합니다.")
                user = self.repository.add(db, username, hash_password(password))
                response = UserResponse(user_id=user.user_id, username=user.username)
                db.commit()
                return response
            except Exception:
                db.rollback()
                raise

    def login(self, username: str, password: str) -> LoginResponse:
        with self.session_factory() as db:
            user = self.repository.find_by_username(db, username)
            valid = verify_password(user.password_hash if user else self._dummy_hash, password)
            if user is None or not valid:
                raise APIError(401, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.")
            result_user = UserResponse(user_id=user.user_id, username=user.username)
        with self._lock:
            if self._closed:
                raise APIError(401, "INVALID_CREDENTIALS", "다시 로그인해 주세요.")
            now = int(self.clock())
            self._prune(now)
            sid = secrets.token_urlsafe(32)
            tokens, record = self._new_tokens(result_user.user_id, sid, now, now + SESSION_SECONDS)
            self._sessions[sid] = record
            self._refresh_index[record.refresh_hash] = sid
            return LoginResponse(user=result_user, **tokens.model_dump())

    def refresh(self, refresh_token: str) -> TokenResponse:
        with self._lock:
            now = int(self.clock())
            self._prune(now)
            digest = refresh_digest(refresh_token)
            sid = self._refresh_index.get(digest)
            record = self._sessions.get(sid)
            if self._closed or record is None or record.run_id != self.run_id:
                raise APIError(401, "INVALID_REFRESH_TOKEN", "유효하지 않은 Refresh 토큰입니다.")
            tokens, replacement = self._new_tokens(record.user_id, sid, now, record.expires_at)
            del self._refresh_index[digest]
            self._sessions[sid] = replacement
            self._refresh_index[replacement.refresh_hash] = sid
            return tokens

    def logout(self, refresh_token: str) -> None:
        with self._lock:
            self._prune(int(self.clock()))
            sid = self._refresh_index.get(refresh_digest(refresh_token))
            if sid is not None:
                self._remove(sid)

    def authenticate(self, access_token: str) -> int:
        try:
            claims = self.signer.decode(access_token)
        except jwt.InvalidTokenError:
            raise self._invalid_access() from None
        with self._lock:
            now = int(self.clock())
            self._prune(now)
            record = self._sessions.get(claims["sid"])
            if (
                self._closed or record is None
                or claims["server_run_id"] != self.run_id
                or record.run_id != self.run_id
                or claims["sub"] != str(record.user_id)
                or claims["exp"] <= now
                or claims["exp"] > record.expires_at
            ):
                raise self._invalid_access()
            return record.user_id

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._sessions.clear()
            self._refresh_index.clear()

    def _new_tokens(self, user_id: int, sid: str, now: int, expires: int):
        refresh_token = secrets.token_urlsafe(32)
        access_expires = min(now + ACCESS_SECONDS, expires)
        tokens = TokenResponse(
            access_token=self.signer.issue(user_id, sid, self.run_id, now, access_expires),
            refresh_token=refresh_token,
            access_expires_in=access_expires - now,
            refresh_expires_in=expires - now,
        )
        return tokens, LoginSession(user_id, self.run_id, expires, refresh_digest(refresh_token))

    def _remove(self, sid: str) -> None:
        record = self._sessions.pop(sid)
        self._refresh_index.pop(record.refresh_hash, None)

    def _prune(self, now: int) -> None:
        for sid in [sid for sid, record in self._sessions.items() if record.expires_at <= now]:
            self._remove(sid)

    @staticmethod
    def _invalid_access() -> APIError:
        return APIError(401, "INVALID_ACCESS_TOKEN", "유효하지 않은 Access 토큰입니다.")
