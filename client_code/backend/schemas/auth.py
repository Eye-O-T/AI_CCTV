"""인증 HTTP 요청·응답 DTO. DB 모델과 분리한다."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


# 명세에서 확정한 최소 길이만 적용한다. 임의 trim/대소문자 변환 없음.
Credential = Annotated[str, StringConstraints(strict=True, min_length=4)]
Token = Annotated[str, StringConstraints(strict=True, min_length=1)]


class SignupStatusResponse(BaseModel):
    signup_required: bool


class SignupRequest(BaseModel):
    username: Credential
    password: Credential = Field(repr=False, json_schema_extra={"writeOnly": True})
    password_confirm: Annotated[str, StringConstraints(strict=True)] = Field(
        repr=False, json_schema_extra={"writeOnly": True}
    )


class LoginRequest(BaseModel):
    username: Credential
    password: Credential = Field(repr=False, json_schema_extra={"writeOnly": True})


class RefreshRequest(BaseModel):
    refresh_token: Token = Field(repr=False, json_schema_extra={"writeOnly": True})


class UserResponse(BaseModel):
    user_id: int
    username: str


class TokenResponse(BaseModel):
    token_type: Literal["bearer"] = "bearer"
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    access_expires_in: int
    refresh_expires_in: int


class LoginResponse(TokenResponse):
    user: UserResponse


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
