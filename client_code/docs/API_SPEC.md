# AI_CCTV 통합 API 명세서

- 작성일: 2026-10-06
- 상태: 인증 API 5개 및 PyQt 인증 화면 연결 구현. API 검증은 [백엔드 README](../backend/README.md), 클라이언트 실행·검증과 제한은 [클라이언트 README](../README.md)를 참고한다.
- 구현 범위: 최초 가입 필요 여부 조회, 최초 회원가입, 로그인, 로그아웃, 토큰 갱신.
- 카메라 구현 범위(2026-10-07): 모델·스키마·Repository·Service·Router 및 등록·목록·상세·부분 수정 API. 임시 DB 자동 테스트와 실제 로컬 HTTP 검증을 완료했다. PyQt 연결·영상 송출은 미구현이다.
- 관리 방식: API 명세는 이 파일 하나에서 관리한다. 카메라·이벤트 등은 합의한 뒤 문서 하단에 `---` 구분선과 독립된 제목으로 추가한다.
- DB 구조와 저장 경계는 [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md)를 함께 참고한다.

## 공통 규칙

- 기본 경로: `/api/v1`.
- 요청 본문: `Content-Type: application/json`.
- 응답: JSON. 단, `204 No Content`는 본문이 없다.
- 표에서 필수로 표시한 값은 누락·`null`을 허용하지 않는다.
- 보호 API는 `Authorization: Bearer <access_token>`으로 인증한다. Refresh 토큰을 일반 API 인증에 사용하지 않는다.
- 비밀번호·토큰 원문을 로그나 오류 응답에 포함하지 않는다. 토큰 원문은 로그인·갱신 성공 응답에서 클라이언트에 전달하는 경우에만 반환한다.
- 아래 JSON의 토큰·비밀번호 문자열은 설명용 자리표시자다.

### 공통 오류 형식

```json
{
  "error": {
    "code": "PASSWORD_CONFIRM_MISMATCH",
    "message": "비밀번호와 비밀번호 확인이 일치하지 않습니다.",
    "field": "password_confirm"
  }
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `error.code` | string | 클라이언트 분기용 오류 코드 |
| `error.message` | string | 사용자 안내 메시지. 클라이언트는 문구 대신 코드로 분기한다. |
| `error.field` | string 또는 null | 관련 입력 필드. 특정 필드 오류가 아니면 null |

- 필수 값 누락, 타입 오류, 길이 위반, 잘못된 JSON은 `422 / VALIDATION_ERROR`로 반환한다.
- FastAPI의 기본 검증 오류도 구현 시 이 형식으로 변환한다. 검증 오류의 원본 입력 값을 그대로 노출하지 않는다.
- 보호 API의 Access 토큰 누락·만료·무효 세션은 `401 / INVALID_ACCESS_TOKEN`으로 처리한다.
- 예상하지 못한 서버 오류는 `500 / INTERNAL_SERVER_ERROR`로 처리하며 내부 예외·DB 정보는 응답에 노출하지 않는다.

---

## 인증 API

### 1. 범위와 공통 제약

| 기능 | Method | 경로 | 인증 수단 |
| --- | --- | --- | --- |
| 최초 가입 필요 여부 조회 | GET | `/api/v1/auth/signup-status` | 인증 불필요 |
| 최초 회원가입 | POST | `/api/v1/auth/signup` | 사전 토큰 없음. 관리자 계정 부재 확인 |
| 로그인 | POST | `/api/v1/auth/login` | 아이디·비밀번호 |
| 로그아웃 | POST | `/api/v1/auth/logout` | 본문의 현재 Refresh 토큰 |
| 토큰 갱신 | POST | `/api/v1/auth/refresh` | 본문의 현재 Refresh 토큰 |

#### 계정과 입력

- 관리자 계정이 없을 때만 최초 회원가입을 허용한다. 계정이 존재하면 추가 가입은 차단한다.
- 가입 가능 여부는 UI뿐 아니라 서버에서 검증한다. 동시 가입 요청으로 두 계정이 생성되지 않도록 가입 가능 여부 확인과 생성을 원자적으로 처리한다.
- `username`, `password`는 문자열이며 각각 4자 이상이다. 길이는 바이트 수가 아닌 문자 수 기준이다.
- 대문자·숫자·특수문자 조합을 강제하지 않는다.
- 회원가입 시 `password_confirm`은 `password`와 정확히 일치해야 한다.
- 비밀번호 확인 값은 저장하지 않는다. 비밀번호는 안전한 비밀번호 해시로만 저장한다.
- 최대 길이·아이디 문자 제한은 아직 미확정이므로 이번 구현에서는 최소 4자만 검사한다. 아이디 대소문자를 구분하고 입력을 trim·정규화하지 않는다. 비밀번호를 임의로 잘라내거나 공백을 자동 제거하지 않는다.
- 비밀번호 해시는 argon2-cffi의 Argon2id를 사용한다.
- 가입 성공은 계정 생성만 의미한다. 클라이언트는 로그인 화면으로 이동하며 자동 로그인하지 않는다.
- 비밀번호 변경은 후속 범위다. 이번 문서에는 엔드포인트를 정의하지 않는다.

#### 토큰·세션 수명

| 대상 | 유효기간 | 기준 |
| --- | --- | --- |
| Access 토큰 | 최대 15분(900초) | 발급 시점부터. 세션 최종 만료 시각을 초과하지 않음 |
| Refresh 토큰 | 최초 로그인부터 8시간(28,800초) | 교체하더라도 최초 로그인 기준 만료 시각 유지 |
| 로그인 세션 | 최초 로그인부터 최대 8시간 | 갱신·활동으로 연장하지 않음 |

- Access 토큰은 PyJWT의 HS256 JWT를 사용한다. issuer는 `ai-cctv-backend`, audience는 `ai-cctv-clients`로 고정한다. 서명 키는 실행마다 안전한 난수 32바이트로 생성하여 메모리에만 둔다.
- Refresh 토큰은 안전한 난수 32바이트를 URL-safe 문자열로 표현하고, 서버에는 SHA-256 해시만 보관한다. 원문을 저장하지 않는다.
- 로그인마다 별도 세션 ID를 생성한다. Access 토큰에 세션 ID와 현재 `server_run_id`를 포함한다.
- 서버는 메모리에 세션 ID, 사용자 ID, 서버 실행 ID, 최종 만료 시각, 현재 Refresh 토큰 해시 등 필요한 상태를 보관한다.
- JWT의 서명·허용 알고리즘·발급자·대상·만료·용도·서버 실행 ID와 메모리 세션의 유효성을 보호 요청마다 검증한다.
- Access 토큰 만료는 자동 갱신 시점이며, 그 자체로 사용자에게 재로그인을 요구하는 시점은 아니다.
- Refresh 토큰은 성공적으로 사용할 때마다 교체하며 이전 토큰은 다시 사용할 수 없다.
- 갱신 시 Access 만료는 `min(발급 시각 + 15분, 세션 최종 만료 시각)`이다. Refresh 만료는 세션 최종 만료 시각을 유지한다.
- 일반 갱신은 이미 발급된 Access 토큰을 즉시 폐기하지 않는다. 해당 토큰은 원래 만료 또는 세션 폐기 시점까지 유효하다.
- 로그아웃하면 해당 세션의 이전·현재 Access 토큰 및 Refresh 토큰을 모두 즉시 무효화한다. 이후 요청에 적용하며 이미 실행 중인 요청을 자동 취소한다는 뜻은 아니다.
- PC와 Flutter는 별도로 로그인한다. 한쪽 로그아웃으로 다른 로그인 세션을 종료하지 않는다.
- 서버 종료·재시작 후 이전 토큰은 모두 거부한다. 클라이언트는 아이디·비밀번호로 다시 로그인한다.
- `auth_sessions`, `refresh_tokens` 테이블은 추가하지 않는다. 백엔드 단일 프로세스·단일 인스턴스에서 메모리로 관리한다.
- 로그아웃과 갱신은 같은 세션에 대해 원자적으로 조정한다. 세션 폐기 후 동시 갱신이 세션을 되살리지 않아야 한다.

예: 09:00 로그인 → 최초 Access 만료 09:15, Refresh·세션 만료 17:00. 09:15에 갱신하면 새 Access 만료는 09:30이며 새 Refresh 만료는 여전히 17:00이다.

### 2. 최초 회원가입

**POST `/api/v1/auth/signup`**

#### 요청

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `username` | string | 예 | 4자 이상 |
| `password` | string | 예 | 4자 이상 |
| `password_confirm` | string | 예 | password와 정확히 일치 |

```json
{
  "username": "admin",
  "password": "<비밀번호>",
  "password_confirm": "<비밀번호 확인>"
}
```

#### 성공: 201 Created

```json
{
  "user_id": 1,
  "username": "admin"
}
```

`user_id`는 생성된 계정의 정수 ID, `username`은 등록된 로그인 아이디다. 토큰·비밀번호·비밀번호 해시는 반환하지 않는다.

#### 실패

| HTTP | 오류 코드 | 조건 |
| --- | --- | --- |
| 409 | `SIGNUP_CLOSED` | 관리자 계정이 이미 존재함 |
| 422 | `PASSWORD_CONFIRM_MISMATCH` | 비밀번호 확인 불일치. field는 password_confirm |
| 422 | `VALIDATION_ERROR` | 필수 값 누락·타입·길이 등 입력 오류 |

### 3. 로그인

**POST `/api/v1/auth/login`**

#### 요청

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `username` | string | 예 | 4자 이상 |
| `password` | string | 예 | 4자 이상 |

```json
{
  "username": "admin",
  "password": "<비밀번호>"
}
```

#### 성공: 200 OK

```json
{
  "user": {
    "user_id": 1,
    "username": "admin"
  },
  "token_type": "bearer",
  "access_token": "<Access 토큰>",
  "refresh_token": "<Refresh 토큰>",
  "access_expires_in": 900,
  "refresh_expires_in": 28800
}
```

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `user.user_id` | integer | 로그인한 관리자 ID |
| `user.username` | string | 로그인한 아이디 |
| `token_type` | string | bearer 고정 |
| `access_token` | string | 보호 API 호출용 토큰 |
| `refresh_token` | string | 갱신·로그아웃 요청용 토큰 |
| `access_expires_in` | integer | 발급 시점 기준 Access 만료까지 남은 초 |
| `refresh_expires_in` | integer | 발급 시점 기준 Refresh·세션 만료까지 남은 초 |

#### 실패

| HTTP | 오류 코드 | 조건 |
| --- | --- | --- |
| 401 | `INVALID_CREDENTIALS` | 아이디 또는 비밀번호 불일치 |
| 422 | `VALIDATION_ERROR` | 필수 값 누락·타입·길이 등 입력 오류 |

계정이 없는 경우와 비밀번호가 틀린 경우는 같은 코드·메시지로 응답한다. 계정 존재 여부를 구분해서 알려주지 않는다.

### 4. 로그아웃

**POST `/api/v1/auth/logout`**

Access 토큰 만료 상태에서도 로그아웃할 수 있도록 Access 인증 헤더는 요구하지 않는다.

#### 요청

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `refresh_token` | string | 예 | 비어 있지 않은 현재 Refresh 토큰 |

```json
{
  "refresh_token": "<현재 Refresh 토큰>"
}
```

#### 성공: 204 No Content

응답 본문 없음.

- 현재 유효한 Refresh 토큰이면 해당 세션과 토큰 기록을 폐기한다.
- 이미 로그아웃했거나 만료·무효인 토큰도 204로 응답한다. 이 경우 유효한 세션을 찾아 폐기했다는 의미는 아니다.
- 클라이언트는 갱신과 로그아웃을 동시에 실행하지 않는다. 진행 중 갱신이 있으면 완료 후 최신 Refresh 토큰으로 로그아웃한다. 로그아웃 이후 늦은 갱신 응답으로 토큰을 다시 저장하지 않는다.
- 클라이언트는 두 토큰을 삭제하고 로그인 화면으로 돌아간다.
- 서버 연결 실패 시 로컬 토큰을 삭제할 수 있지만, 서버 세션 폐기 성공으로 표시하지 않는다.

#### 실패

| HTTP | 오류 코드 | 조건 |
| --- | --- | --- |
| 422 | `VALIDATION_ERROR` | Refresh 토큰 필드 누락·null·빈 문자열·타입 오류 |

### 5. 토큰 갱신

**POST `/api/v1/auth/refresh`**

Access 인증 헤더는 요구하지 않는다.

#### 요청

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `refresh_token` | string | 예 | 비어 있지 않은 현재 Refresh 토큰 |

```json
{
  "refresh_token": "<현재 Refresh 토큰>"
}
```

#### 성공: 200 OK

09:00 로그인 후 09:15에 갱신한 예시:

```json
{
  "token_type": "bearer",
  "access_token": "<새 Access 토큰>",
  "refresh_token": "<새 Refresh 토큰>",
  "access_expires_in": 900,
  "refresh_expires_in": 27900
}
```

- 필드 타입·의미는 로그인 응답의 토큰 필드와 동일하다. user 객체는 반환하지 않는다.
- `refresh_expires_in`은 항상 28800으로 초기화하지 않는다. 최초 로그인 기준 8시간 중 실제 남은 초를 반환한다.
- 세션의 최종 만료까지 15분 미만이면 `access_expires_in`도 그 남은 시간으로 제한한다.
- 검증·이전 Refresh 제거·새 토큰 등록은 원자적으로 처리한다. 동일 Refresh를 사용한 동시 요청 중 최대 하나만 성공한다.
- 클라이언트는 갱신 요청을 한 번에 하나만 수행하고, 성공 시 두 토큰을 함께 교체한다.
- 갱신 성공 응답 유실 후 이전 Refresh로 재시도하면 401이 될 수 있다. MVP는 이전 토큰의 재사용 유예를 제공하지 않으며, 새 토큰을 확보하지 못하면 재로그인한다.

#### 실패

| HTTP | 오류 코드 | 조건 |
| --- | --- | --- |
| 401 | `INVALID_REFRESH_TOKEN` | 만료·변조·이미 사용한 토큰, 로그아웃·서버 재시작으로 무효인 세션 |
| 422 | `VALIDATION_ERROR` | Refresh 토큰 필드 누락·null·빈 문자열·타입 오류 |

401이면 클라이언트는 토큰을 삭제하고 재로그인을 요구한다. 단순 네트워크 오류를 인증 실패로 단정하지 않는다.

### 6. 구현 및 검증 기준

- Router → Service → Repository 구조를 유지하고, PyQt는 HTTP로만 접근한다.
- DB 모델을 API 응답으로 직접 노출하지 않는다.
- 최초 가입 성공·두 번째 가입 거부·동시 가입 시 계정 하나만 생성되는지 검증한다.
- 입력 길이 경계(3자 거부·4자 허용), 확인 불일치, 잘못된 계정 정보, 비밀값 미노출을 검증한다.
- 갱신 성공·이전 Refresh 재사용 거부·동시 갱신·최종 8시간 만료를 검증한다.
- 갱신으로 최대 로그인 시간이 연장되지 않고 Access도 세션 만료를 넘지 않는지 검증한다.
- 로그아웃 후 해당 세션의 이전·현재 Access와 Refresh 모두 거부되는지, 다른 세션은 유지되는지 검증한다.
- 서버 재시작 후 모든 이전 세션이 거부되는지 검증한다.
- 실제 PyQt 가입 → 로그인 → 관제 화면 → 로그아웃 → 재로그인은 API 단위 검증과 구분하여 확인한다.
- PyQt는 로컬 백엔드에 연결하며 토큰은 메모리에만 보관한다. 로그아웃은 영상 수신·녹화·분석·부가 작업 종료 확인 후 서버 로그아웃 → 토큰 삭제 → 로그인 화면 순서다. 실제 장치 검증 범위는 클라이언트 README를 따른다.

### 7. 이후 명세 추가 범위

카메라 관리 계약은 아래 카메라 API 절에 정의한다. 이벤트·비밀번호 변경·영상 송출 API는 후속 범위이며 아직 계약을 정의하지 않았다.

---

## 인증 API 추가: 최초 관리자 가입 필요 여부 조회

**GET `/api/v1/auth/signup-status`**

PyQt 시작 시 회원가입 창과 로그인 창 중 어느 화면을 표시할지 판단하기 위한 조회다.

### 요청

- 인증 헤더, 요청 본문, 경로·쿼리 매개변수가 필요 없다.
- PyQt는 DB를 직접 조회하지 않고 이 API를 호출한다.

### 성공: 200 OK

관리자 계정이 없을 때:

```json
{
  "signup_required": true
}
```

관리자 계정이 이미 있을 때:

```json
{
  "signup_required": false
}
```

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `signup_required` | boolean | true: 관리자 없음 → 회원가입 창, false: 관리자 있음 → 로그인 창 |

### 제약과 실패 처리

- 서버의 `users` 존재 여부를 조회하며 데이터를 변경하지 않는다. 아이디·사용자 ID·계정 수·비밀번호 등 계정 정보는 반환하지 않는다.
- 성공 응답에 `Cache-Control: no-store`를 설정하여 이전 조회 결과가 캐시에 남지 않게 한다.
- `true`는 조회 시점의 상태이며 가입 권한을 예약하지 않는다. 회원가입 요청에서는 계정 부재를 다시 원자적으로 검증한다. 조회 후 다른 요청이 먼저 가입하면 회원가입 API는 `409 / SIGNUP_CLOSED`를 반환한다.
- 회원가입 성공 후 로그인 화면으로 이동한다. 자동 로그인하지 않는다.
- DB 조회 등 내부 실패는 공통 형식의 `500 / INTERNAL_SERVER_ERROR`다. 오류를 관리자 없음(true)으로 변환하지 않는다.
- 서버 연결 실패·시간 초과·비정상 응답에서는 화면을 임의로 선택하지 않고 연결 오류와 재시도를 안내한다.
- PyQt 시작 시 이 API로 회원가입·로그인 화면을 분기한다. API 호출 실패는 연결 오류·재시도로 처리한다.

---

## 카메라 API

### 1. 범위와 공통 규칙

카메라 등록 정보를 SQLite에 저장하고 조회·수정하기 위한 관리자 API다. 실제 영상 연결, 연결 상태 보고, 영상 송출, PyQt 화면 연결은 이번 계약에 포함하지 않는다.

| 기능 | Method | 경로 | 성공 |
| --- | --- | --- | --- |
| 등록 | POST | `/api/v1/cameras` | 201 |
| 목록 조회 | GET | `/api/v1/cameras` | 200 |
| 상세 조회 | GET | `/api/v1/cameras/{camera_id}` | 200 |
| 부분 수정·비활성화 | PATCH | `/api/v1/cameras/{camera_id}` | 200 |

- 모든 요청은 `Authorization: Bearer <access_token>`으로 기존 관리자 인증을 적용한다. Refresh 토큰은 사용할 수 없다.
- `camera_id`는 1 이상의 정수다. 형식 오류·0·음수는 422, 올바른 ID 형식이지만 등록되지 않은 경우는 404다.
- 요청 JSON에 정의되지 않은 필드나 읽기 전용 필드가 있으면 422로 거부한다.
- JSON 정수 필드는 문자열·실수·boolean을 정수로 자동 변환하지 않는다. boolean 필드는 JSON `true` 또는 `false`만 허용한다.
- 날짜는 UTC의 ISO 8601 문자열로 반환한다. 예: `2026-10-06T03:00:00.000000Z`.
- 등록·수정 시 실제 카메라 접속이나 영상 수신을 수행하지 않는다. 연결되지 않는 장치도 형식과 제약이 유효하면 저장한다.
- `is_enabled`는 사용 설정이다. `true`가 현재 연결 중이라는 뜻은 아니며, `false` 저장만으로 실행 중 수신기가 즉시 종료된다는 의미도 아니다. 실행 반영은 이후 PyQt 연동에서 처리한다.
- DELETE API는 제공하지 않는다. 비활성화한 등록 정보도 보존하고 재활성화할 수 있다.

### 2. 입력 제약과 중복 기준

| 필드 | 규칙 |
| --- | --- |
| `name` | 문자열. 앞뒤 공백 제거 후 1~100자. 내부 공백 유지, 이름 중복 허용 |
| `camera_type` | `webcam` 또는 `edge_node`. 등록 후 변경 불가 |
| `webcam_index` | 0 이상의 정수. `webcam`이면 필수, `edge_node`이면 null |
| `rtsp_url` | `edge_node`이면 필수. `rtsp://` 또는 `rtsps://`와 유효한 호스트 필요. `webcam`이면 null |
| `edge_api_base_url` | `edge_node`에서 선택. `http://` 또는 `https://`와 유효한 호스트 필요. `webcam`이면 null |
| `is_enabled` | boolean. 등록 시 생략하면 true |

#### 주소 처리

- 주소는 앞뒤 공백을 제거하고 저장한다. 빈 문자열은 null로 바꾸지 않고 422로 거부한다. 선택 주소를 없애려면 null을 보낸다.
- 주소 안의 공백·제어 문자, URL fragment(`#...`), 잘못된 포트는 거부한다. 포트를 명시하면 1~65535여야 한다.
- RTSP 주소의 경로·일반 쿼리는 허용한다. `edge_api_base_url`에는 경로를 허용하되 쿼리는 허용하지 않는다.
- URL userinfo(`사용자:비밀번호@호스트`)는 거부한다. 토큰·비밀번호 등 자격증명을 쿼리나 경로에 포함해서도 안 된다. 임의 쿼리 값의 비밀 여부를 자동 판별할 수 있다고 가정하지 않는다. 장치 인증이 필요한 경우 별도 자격증명 계약을 마련한 뒤 지원한다.
- 앞뒤 공백 제거 외에 주소를 자동 재작성하지 않는다. 호스트 대소문자, 기본 포트 생략, 끝 슬래시 등 다른 표기의 동등성은 이번 중복 판정에 포함하지 않는다.
- URL 및 검증 실패 원본 입력을 로그·오류 응답에 기록하지 않는다.

#### 종류별 조합과 중복

- `webcam`: `webcam_index` 필수, `rtsp_url`·`edge_api_base_url`은 생략 또는 null만 허용한다.
- `edge_node`: `rtsp_url` 필수, `webcam_index`는 생략 또는 null만 허용한다. `edge_api_base_url`은 복구·제어 API 연동 전까지 선택이다.
- `webcam_index`는 등록된 웹캠 전체에서 중복을 허용하지 않는다. 단일 관제 PC의 장치 번호를 전제로 한다.
- `rtsp_url`은 앞뒤 공백 제거 후 문자열이 정확히 같은 경우 중복으로 처리한다. 서로 다른 주소가 같은 물리 장치인지는 판별하지 않는다.
- 중복 검사에 비활성 카메라도 포함한다. 수정 시 자기 자신은 제외한다.
- 종류별 필수·금지 조합, 이름 길이, 사용 여부, 장치 번호 범위와 중복 제약은 DB에서도 보장한다. 동시 등록·수정으로 중복이 생기지 않아야 한다.

### 3. 응답 스키마

목록용 `CameraSummary`와 관리 상세용 `CameraResponse`를 구분한다. DB 모델을 그대로 응답으로 반환하지 않는다.

| 필드 | 타입 | 목록 | 상세·등록·수정 | 설명 |
| --- | --- | --- | --- | --- |
| `camera_id` | integer | 포함 | 포함 | 서버가 발급하는 고유 ID |
| `name` | string | 포함 | 포함 | 정리된 표시 이름 |
| `camera_type` | string | 포함 | 포함 | webcam 또는 edge_node |
| `is_enabled` | boolean | 포함 | 포함 | 사용 설정 |
| `created_at` | string | 포함 | 포함 | UTC 등록 시각 |
| `updated_at` | string | 포함 | 포함 | UTC 마지막 변경 시각 |
| `webcam_index` | integer 또는 null | 제외 | 포함 | 웹캠 입력 번호 |
| `rtsp_url` | string 또는 null | 제외 | 포함 | 관리자용 원본 수신 주소 |
| `edge_api_base_url` | string 또는 null | 제외 | 포함 | 관리자용 장치 API 주소 |

- 상세 응답은 PyQt의 설정 편집·입력 연결에 필요한 관리 정보다. 목록에는 원본 주소를 노출하지 않는다.
- 현재 인증은 단일 관리자 체계다. 같은 관리자 토큰으로는 상세 조회도 가능하며, PyQt와 Flutter를 보안상 구분하는 권한 정책은 없다.
- Flutter 시청용 주소는 이 관리 상세 응답으로 대체하지 않는다. 향후 시청 권한·영상 송출 계약에서 별도로 정의한다.
- 연결 상태, FPS, 재생 URL 등 런타임 정보는 응답에 포함하지 않는다.

### 4. 카메라 등록

**POST `/api/v1/cameras`**

요청 스키마: `CameraCreate`.

| 필드 | 필수 | 생략 시 |
| --- | --- | --- |
| `name` | 예 | 오류 |
| `camera_type` | 예 | 오류 |
| `webcam_index` | webcam에서 필수 | null |
| `rtsp_url` | edge_node에서 필수 | null |
| `edge_api_base_url` | 아니요 | null |
| `is_enabled` | 아니요 | true |

웹캠 등록 예시:

```json
{
  "name": "정문",
  "camera_type": "webcam",
  "webcam_index": 0
}
```

엣지노드 등록 예시:

```json
{
  "name": "후문",
  "camera_type": "edge_node",
  "rtsp_url": "rtsp://192.0.2.10:8554/live",
  "edge_api_base_url": "http://192.0.2.10:8001",
  "is_enabled": true
}
```

주소는 문서용 예시이며 실제 연결을 보장하지 않는다.

#### 성공: 201 Created

`CameraResponse`를 반환하며 `Location: /api/v1/cameras/1`처럼 생성된 리소스 경로를 응답 헤더에 설정한다.

```json
{
  "camera_id": 1,
  "name": "정문",
  "camera_type": "webcam",
  "webcam_index": 0,
  "rtsp_url": null,
  "edge_api_base_url": null,
  "is_enabled": true,
  "created_at": "2026-10-06T03:00:00.000000Z",
  "updated_at": "2026-10-06T03:00:00.000000Z"
}
```

- 생성 시 두 시각은 동일하다. 클라이언트가 ID나 시각을 지정할 수 없다.
- 성공은 DB 저장 완료를 의미한다. 응답을 잃고 같은 입력으로 재시도하면 중복 오류가 발생할 수 있으므로 목록·상세로 확인한다.
- 실패: 공통 인증·입력·서버 오류 및 `409 / CAMERA_SOURCE_CONFLICT`.

### 5. 카메라 목록 조회

**GET `/api/v1/cameras`**

요청 본문 없음. 응답 스키마: `CameraListResponse`.

| 쿼리 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `is_enabled` | boolean | 아니요 | 생략: 전체, `true`: 활성만, `false`: 비활성만 |

- 쿼리 값은 문자열 `true`, `false`만 허용한다. 빈 값·다른 표기는 422다.
- 초기에는 전체 목록을 `camera_id` 오름차순으로 반환한다. 페이지네이션·검색은 제공하지 않는다.

#### 성공: 200 OK

```json
{
  "items": [
    {
      "camera_id": 1,
      "name": "정문",
      "camera_type": "webcam",
      "is_enabled": true,
      "created_at": "2026-10-06T03:00:00.000000Z",
      "updated_at": "2026-10-06T03:00:00.000000Z"
    }
  ]
}
```

등록된 카메라가 없거나 필터 결과가 없으면 `200`과 `{"items": []}`를 반환한다. 기본 CAM-01을 자동 생성하지 않는다.

실패: 공통 인증·입력·서버 오류.

### 6. 카메라 상세 조회

**GET `/api/v1/cameras/{camera_id}`**

요청 본문 없음. 성공 시 `200 OK`와 등록 응답과 동일한 `CameraResponse`를 반환한다. 비활성 카메라도 조회 가능하다.

실패: 공통 인증·입력·서버 오류 및 `404 / CAMERA_NOT_FOUND`.

### 7. 카메라 부분 수정·비활성화

**PATCH `/api/v1/cameras/{camera_id}`**

요청 스키마: `CameraUpdate`.

| 수정 가능 필드 | 생략 | 명시적 null |
| --- | --- | --- |
| `name` | 기존 값 유지 | 거부 |
| `webcam_index` | 기존 값 유지 | webcam이면 거부, edge_node이면 허용 |
| `rtsp_url` | 기존 값 유지 | edge_node이면 거부, webcam이면 허용 |
| `edge_api_base_url` | 기존 값 유지 | 주소 제거 |
| `is_enabled` | 기존 값 유지 | 거부 |

- 빈 객체 `{}`는 422다. 수정 가능 필드를 하나 이상 전달한다.
- `camera_type`은 같은 값을 보내더라도 읽기 전용이므로 422다. `camera_id`, `created_at`, `updated_at`도 요청할 수 없다.
- 기존 값에 요청 값을 합친 최종 상태를 기준으로 종류별 조합·주소·중복 제약을 검증한다.
- 모든 변경은 한 트랜잭션으로 반영한다. 일부 필드가 실패하면 전체 변경을 취소한다.
- 서로 다른 필드의 동시 수정은 생략된 필드를 덮어쓰지 않는다. 같은 필드의 동시 수정은 마지막으로 반영된 값을 사용하며 버전 충돌 검사는 제공하지 않는다.

이름 수정:

```json
{
  "name": "정문 출입구"
}
```

비활성화:

```json
{
  "is_enabled": false
}
```

엣지노드 API 주소 제거:

```json
{
  "edge_api_base_url": null
}
```

#### 성공: 200 OK

수정 후 전체 `CameraResponse`를 반환한다. 실제 저장값이 변경되면 `updated_at`을 갱신하고, 같은 값을 다시 보내 변경이 없으면 기존 시각을 유지한다. `camera_id`, `camera_type`, `created_at`은 유지한다.

실패: 공통 인증·입력·서버 오류 및 `404 / CAMERA_NOT_FOUND`, `409 / CAMERA_SOURCE_CONFLICT`.

### 8. 오류 계약

문서 상단의 공통 오류 JSON 형식을 사용한다.

| HTTP | 오류 코드 | 조건 | field |
| --- | --- | --- | --- |
| 401 | `INVALID_ACCESS_TOKEN` | 토큰 누락·만료·변조·무효 세션 | null |
| 404 | `CAMERA_NOT_FOUND` | 등록되지 않은 양의 정수 ID | camera_id |
| 409 | `CAMERA_SOURCE_CONFLICT` | 다른 등록과 입력 소스 중복 | webcam_index 또는 rtsp_url |
| 422 | `VALIDATION_ERROR` | 필수값·타입·범위·주소·종류 조합 오류, 읽기 전용·알 수 없는 필드, 빈 PATCH | 관련 필드 또는 null |
| 500 | `INTERNAL_SERVER_ERROR` | 예상하지 못한 서버·DB 오류 | null |

```json
{
  "error": {
    "code": "CAMERA_SOURCE_CONFLICT",
    "message": "이미 등록된 카메라 입력입니다.",
    "field": "webcam_index"
  }
}
```

검증 실패 응답에 요청 원본, URL, DB 예외를 넣지 않는다. 여러 입력 오류의 반환 우선순서는 보장하지 않으며 클라이언트는 오류 코드와 field로 처리한다.

### 9. 구현·검증 기준과 다음 단계

- 구현 순서: SQLAlchemy Camera 모델 → Pydantic 스키마 → Repository·Service·Router → API 검증 → PyQt 연결.
- 트랜잭션 범위는 Service에서 관리한다. 입력 소스 중복은 DB UNIQUE 제약으로도 보장하고 해당 위반만 409로 변환한다.
- 기존 계정·서버 실행 기록을 보존하며 cameras 테이블을 추가한다. 운영 DB 초기화로 구현하지 않는다.
- 등록 → 목록·상세 → 수정 → 비활성화 → 필터 조회 → 재활성화 흐름을 임시 DB로 검증한다.
- 서버 재시작 후 다시 로그인하여 카메라 등록 정보가 유지되는지 검증한다.
- 이름 길이 경계, 음수·boolean 장치 번호, 잘못된 종류 조합, URL 자격증명·형식 오류, PATCH 생략/null 차이와 읽기 전용 필드 거부를 검증한다.
- 활성·비활성 소스 중복, 동시 등록·수정 중복, 변경 실패 시 전체 롤백, 존재하지 않는 ID와 무인증·만료·로그아웃 토큰을 검증한다.
- 카메라 연결 불가 상태에서도 등록이 가능하고, API 처리에서 장치 접속을 시도하지 않는지 확인한다.
- 기존 인증 회귀 검증을 수행한다. API 테스트는 실제 카메라 수신·PyQt 표시·Flutter 재생 검증과 구분한다.
- 2026-10-07 Camera 모델·스키마·Repository·Service·Router와 API 검증을 완료했다. 전체 38개 테스트와 임시 DB 실제 Uvicorn HTTP 검증이 통과했다. 상세 결과는 백엔드 README를 참고한다. PyQt 연결과 영상 송출은 미구현이다.
