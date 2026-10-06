from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ..dependencies import get_auth_service
from ..schemas.auth import (
    ErrorResponse, LoginRequest, LoginResponse, RefreshRequest,
    SignupRequest, TokenResponse, UserResponse,
)
from ..services.auth_service import AuthService


router = APIRouter(
    prefix="/api/v1/auth", tags=["인증"],
    responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
Auth = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/signup", status_code=201, response_model=UserResponse,
             responses={409: {"model": ErrorResponse}})
def signup(body: SignupRequest, service: Auth) -> UserResponse:
    return service.signup(body.username, body.password, body.password_confirm)


@router.post("/login", response_model=LoginResponse,
             responses={401: {"model": ErrorResponse}})
def login(body: LoginRequest, service: Auth, response: Response) -> LoginResponse:
    response.headers["Cache-Control"] = "no-store"
    return service.login(body.username, body.password)


@router.post("/logout", status_code=204, response_class=Response)
def logout(body: RefreshRequest, service: Auth) -> Response:
    service.logout(body.refresh_token)
    return Response(status_code=204)


@router.post("/refresh", response_model=TokenResponse,
             responses={401: {"model": ErrorResponse}})
def refresh(body: RefreshRequest, service: Auth, response: Response) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    return service.refresh(body.refresh_token)
