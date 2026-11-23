"""비밀번호 해시와 Access JWT 서명·검증. 세션 정책은 Service에서 처리."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
import jwt


ISSUER = "ai-cctv-backend"
AUDIENCE = "ai-cctv-clients"
ALGORITHM = "HS256"
password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def refresh_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenSigner:
    def __init__(self):
        # 실행별 새 키: 재시작·DB 복원 시 이전 토큰이 다시 유효해지지 않는다.
        self._key = secrets.token_bytes(32)

    def issue(self, user_id: int, session_id: str, run_id: int, now: int, expires: int) -> str:
        return jwt.encode(
            {"sub": str(user_id), "sid": session_id, "server_run_id": run_id,
             "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": expires,
             "token_use": "access", "jti": secrets.token_urlsafe(16)},
            self._key, algorithm=ALGORITHM,
        )

    def decode(self, token: str) -> dict:
        claims = jwt.decode(
            token, self._key, algorithms=[ALGORITHM], issuer=ISSUER, audience=AUDIENCE,
            options={"require": ["sub", "sid", "server_run_id", "iss", "aud", "iat", "exp", "token_use", "jti"]},
        )
        if (
            claims["token_use"] != "access"
            or not isinstance(claims["sid"], str)
            or not isinstance(claims["sub"], str)
            or not claims["sub"].isascii()
            or not claims["sub"].isdigit()
            or type(claims["server_run_id"]) is not int
            or type(claims["exp"]) is not int
            or type(claims["iat"]) is not int
        ):
            raise jwt.InvalidTokenError("Invalid access claims")
        return claims
