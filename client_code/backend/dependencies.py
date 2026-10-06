"""앱의 인증 서비스와 보호 API용 Access 인증 의존성."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import APIError
from .services.auth_service import AuthService


bearer = HTTPBearer(auto_error=False)


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def require_user_id(
    service: Annotated[AuthService, Depends(get_auth_service)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> int:
    if credentials is None:
        raise APIError(401, "INVALID_ACCESS_TOKEN", "유효하지 않은 Access 토큰입니다.")
    return service.authenticate(credentials.credentials)
