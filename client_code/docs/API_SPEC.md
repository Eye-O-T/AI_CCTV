# AI_CCTV 통합 API 명세서

- 작성일: 2026-10-06
- 상태: 인증 API 5개 및 PyQt 인증 화면 연결 구현. API 검증은 [백엔드 README](../backend/README.md), 클라이언트 실행·검증과 제한은 [클라이언트 README](../README.md)를 참고한다.
- 구현 범위: 최초 가입 필요 여부 조회, 최초 회원가입, 로그인, 로그아웃, 토큰 갱신.
- 카메라 구현 범위(2026-10-07): 모델·스키마·Repository·Service·Router 및 등록·목록·상세·부분 수정 API. 임시 DB 자동 테스트와 실제 로컬 HTTP 검증을 완료했다. PyQt 관리는 연결되어 있으며, 2026-10-08 최대 두 카메라 실행·전체/개별 보기까지 구현했다. 클라이언트 동시 실행은 AI OFF 합성 입력으로 검증했으며 실제 장치·YOLO 동시 성능은 미검증이다. Flutter용 영상 송출은 미구현이다.
- 관리 방식: API 명세는 이 파일 하나에서 관리한다. 카메라·이벤트 등은 합의한 뒤 문서 하단에 `---` 구분선과 독립된 제목으로 추가한다.
- DB 구조와 저장 경계는 [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md)를 함께 참고한다.
- ReID 1~5단계(2026-11-19): 글로벌 인물 API·모델·PC OSNet·공용 Manager·카메라 큐·인증 수명·Local/Global 표시를 구현했다. 사용자 실촬영의 단일 인물 연결과 나머지 정확도/성능 미검증 범위를 개발 계획에 구분한다. 5단계에서 API 계약은 변경하지 않았다.

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

카메라 관리 계약은 아래 카메라 API 절에 정의한다. 이번 멀티카메라 ReID MVP의 계약은 하단 「글로벌 인물 API」 절에 정의한다. 이벤트 생성·매칭 결과의 이벤트 연결은 후속 단계에서 정의하며 이번 파이프라인은 event_id 없이 동작한다. 이벤트 히스토리·미디어·CNN·궤적·클립 갱신, 비밀번호 변경·영상 송출도 후속 범위다.

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
- 2026-10-07 Camera 모델·스키마·Repository·Service·Router와 API 검증을 완료했다. 전체 38개 테스트와 임시 DB 실제 Uvicorn HTTP 검증이 통과했다. 상세 결과는 백엔드 README를 참고한다. PyQt 관리·단일 카메라 실행을 연결했다. Flutter용 영상 송출은 미구현이다.

---

## 글로벌 인물 API — 멀티카메라 ReID MVP

### 1. 범위와 구현 상태

- 2026-11-18 1단계: **이 절의 글로벌 인물 DB 모델·3개 API를 구현했다.** 백엔드 53개·임시 HTTP 검증 후 2~5단계에서 OSNet·Manager·카메라 큐·인증 수명·영상 표시를 연결했다. API 계약은 그대로이며 단일 인물의 두 카메라 연결은 사용자 확인했다. 나머지 실장치 정확도/성능은 [GLOBAL_PERSON_DEVELOPMENT_PLAN.md](GLOBAL_PERSON_DEVELOPMENT_PLAN.md)에 미검증으로 구분한다.
- 이번 구현은 카메라별 CropWorker → OSNetWorker → 공용 GlobalPersonManager → 글로벌 인물 DB 저장·화면 표시까지다. 이벤트 생성·event_id·매칭 상태 저장·CNN·미디어 송출은 의존하지 않는다.
- 이벤트는 아래 PC 이벤트 MVP 계약을 따른다. 과거 10컬럼·global-match API 설계는 폐기한다. 글로벌 인물 API는 이벤트 생성 응답에 의존하지 않는다.
- Manager 한 개가 비교 → 기존/신규 결정 → API 저장 확인 → 메모리 반영을 순차 처리한다. 백엔드는 특징 추출·유사도 계산을 수행하지 않는다.
- 비교 가능한 후보의 최고 코사인 유사도가 임계값 이상이면 기존 인물, 미만이거나 후보가 없으면 신규 인물이다. 같은 카메라에서 현재 관측 중인 다른 인물이 사용하는 글로벌 ID는 후보에서 제외하며 동점은 작은 ID를 선택한다. deferred는 사용하지 않는다.

| 기능 | Method | 경로 | 성공 |
| --- | --- | --- | --- |
| 전체 후보·대표 벡터 조회 | GET | `/api/v1/global-persons` | 200 |
| 신규 글로벌 인물 등록 | POST | `/api/v1/global-persons` | 신규 201, 동일 요청 재확인 200 |
| 기존 인물 마지막 관측 갱신 | PATCH | `/api/v1/global-persons/{global_person_id}/last-seen` | 200 |

### 2. 공통 규칙과 DTO

- 모든 요청에 기존 관리자 Access 인증을 적용한다. PC와 Flutter의 별도 권한 구분은 없다. Router → Service → Repository 구조를 유지하며 PC는 DB를 직접 수정하지 않는다.
- 요청에 정의되지 않은 필드·명시적 null을 거부한다. JSON 정수 ID는 양수이며 문자열·실수·boolean에서 자동 변환하지 않는다.
- observed_at은 실제 crop 관측 시각이다. 시간대를 포함한 ISO 8601을 받고 UTC로 정규화한다. 서버가 created_at·updated_at을 부여하고 응답 날짜는 UTC Z 형식이다.
- feature_vector는 **정확히 512개**의 JSON 숫자 배열이다. 숫자 정수·실수는 허용하지만 boolean·문자열·null·중첩 배열·NaN·무한대를 거부한다. float32 변환 후 유한한 비영벡터이며 |L2 norm−1| ≤ 0.001이어야 한다.
- DB 저장은 little-endian float32 BLOB, **2048바이트**다. 백엔드에서 변환하며 조용한 차원 보정·잘라내기·영벡터 대체는 하지 않는다.
- 기본 프로파일은 OSNet x1.0 / MSMT17 combineall / 256×128 / RGB / mean·std 정규화 / 이미지별 L2 → 5장 평균 → L2다. 상세 값은 개발 계획에 고정한다. 모델·가중치·전처리가 다른 벡터는 길이가 같아도 혼합하지 않는다.
- 파일 경로·카메라 ID·로컬 ID·작업 ID는 PC의 관측 연결 정보다. 이번 글로벌 인물 API와 테이블에는 저장하지 않는다. 사진 업로드·파일 접근 API를 추가하지 않는다.
- 글로벌 인물 응답은 아래 전용 DTO다. 벡터는 실제 길이 512의 배열이며 표에서는 전체 숫자를 생략한다.

| 응답 필드 | 타입 | 의미 |
| --- | --- | --- |
| `global_person_id` | integer | DB가 발급한 글로벌 ID |
| `feature_vector` | number[512] | 최초 대표 벡터 |
| `created_at` | string | 글로벌 인물 DB 등록 시각 |
| `updated_at` | string | 대표 벡터 갱신 시각. 최초 생성 이후 이번 MVP에서는 유지 |
| `last_seen_at` | string | 가장 늦은 연결 관측 시각 |

- 벡터·전체 요청·토큰·내부 DB 예외를 오류 응답/로그에 노출하지 않는다. 예상하지 못한 오류는 공통 500이며 원본 입력을 응답에 넣지 않는다.

### 3. 전체 후보 조회

**GET `/api/v1/global-persons`**

- 본문·쿼리 없음. 성공 응답은 `{"feature_dimension":512,"items":[]}` 형식이며 items에 글로벌 인물 DTO를 ID 오름차순으로 담는다. 인물이 없으면 빈 배열이다.
- 초기 졸업작품 규모에서는 전체 조회로 시작하며 페이지네이션·벡터 검색 DB는 추가하지 않는다. Cache-Control: no-store를 적용한다.
- Manager 시작·재시작 시 인증 후 후보를 복원한 뒤 매칭을 시작한다. 매 관측마다 전체 재조회하지 않는다.
- 저장된 벡터의 길이·유한성·norm 검증/복원이 실패하면 500이다. 불량 후보를 조용히 누락하거나 빈 목록 성공으로 바꾸지 않는다. 후보 초기화 실패 중에는 매칭을 시작하지 않는다.

### 4. 신규 인물 등록 및 재시도

**POST `/api/v1/global-persons`**

| 요청 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `request_id` | string(UUID v4) | 예 | 신규 등록 재시도 식별자. 최초 제출 시 한 번 생성 |
| `feature_vector` | number[512] | 예 | 정규화된 최초 대표 벡터 |
| `observed_at` | string | 예 | 해당 관측 시각 |

- Manager가 신규로 판단한 경우에만 호출한다. ID는 클라이언트가 지정하지 않는다. 이 API는 유사도를 재판정하지 않는다.
- Service 트랜잭션에서 INSERT 후 AUTOINCREMENT ID를 받는다. created_at=updated_at=서버 등록 시각, last_seen_at=observed_at이다. 성공 응답은 글로벌 인물 DTO다.
- **단일 백엔드 실행의 메모리**에 (인증 사용자 ID, request_id)별 정규화된 입력과 발급 결과를 보관한다. 키·비교·트랜잭션·결과 등록을 잠금으로 조정하여 같은 요청의 동시 등록을 막는다. Repository는 commit하지 않는다.
- 최초 커밋 성공은 201, 동일 키·동일 float32 벡터 바이트·동일 UTC observed_at 재전송은 200으로 같은 ID와 현재 DTO를 반환한다. 같은 키에 다른 내용이면 409 / GLOBAL_PERSON_REQUEST_CONFLICT다. 실패/롤백한 등록은 성공 기록으로 남기지 않는다.
- 이 기록은 새 DB 테이블·컬럼을 추가하지 않고 서버 실행 동안 유지한다. 서버 재시작은 키 기록을 초기화하므로 PC도 이전 미확정 작업·request_id를 비운다. 이전 요청을 새 로그인 뒤 자동 재전송하지 않는다. 재시작 후에는 전체 후보를 복원하고 새 관측부터 처리한다.
- 저장 성공 뒤 응답만 유실됐다면 같은 request_id와 같은 본문으로 재전송한다. 다른 ID를 새로 만들어 재시도하거나 다음 신규 판단을 먼저 진행하지 않는다. 커밋 직후 프로세스가 종료된 경우도 재시작 규칙을 적용한다. 영속적 재시도 이력은 이번 MVP 범위가 아니다.
- 성공 확인 후 반환된 ID와 최초 벡터를 Manager 후보에 반영한다. 대표 벡터 갱신·삭제 API는 제공하지 않는다.

### 5. 기존 인물 마지막 관측 갱신

**PATCH `/api/v1/global-persons/{global_person_id}/last-seen`**

```json
{"observed_at":"2026-11-18T03:00:00.000000Z"}
```

- 요청 필드는 observed_at 하나다. 글로벌 인물 부재는 404 / GLOBAL_PERSON_NOT_FOUND이며 신규 인물을 대신 만들지 않는다.
- Service 트랜잭션 안에서 last_seen_at=max(기존 값, observed_at)으로 갱신한다. 같은/과거 시각을 재전송하면 변경 없이 200이다. 동시 요청도 가장 늦은 시각을 유지한다.
- feature_vector·created_at·updated_at은 변경하지 않는다. 성공은 글로벌 인물 DTO다.
- Worker는 첫 5장으로 관측당 대표 벡터 한 개를 만든다. 기존 인물로 연결할 때 한 번 갱신하며 프레임마다 heartbeat를 보내지 않는다. 따라서 last_seen_at은 마지막 프레임 시각이 아니라 **매칭된 관측의 시각**이다.
- Manager는 저장 확인 후 현재 관측 연결을 반영한다. 영상 스레드는 HTTP 응답을 기다리지 않는다. 실패하면 재확인 전 다음 신규 판단을 멈추되 영상 수신은 유지한다.

### 6. 오류와 검증 기준

| HTTP | 오류 코드 | 조건 |
| --- | --- | --- |
| 401 | INVALID_ACCESS_TOKEN | Access 누락·만료·무효 |
| 404 | GLOBAL_PERSON_NOT_FOUND | 마지막 관측 갱신 대상 부재 |
| 409 | GLOBAL_PERSON_REQUEST_CONFLICT | 같은 request_id의 다른 등록 내용 |
| 422 | VALIDATION_ERROR | UUID·타입·512차원·수치·norm·시각·금지 필드 오류 |
| 500 | INTERNAL_SERVER_ERROR | DB·저장 벡터 복원 등 내부 오류 |

- 1단계에서 임시 DB로 신규 등록·조회·float32 BLOB 왕복·관측 시각의 역행 방지·최초 벡터 유지·인증·잘못된 입력을 검증했다. 전체 53개 중 글로벌 인물 신규 검증은 15개다. 아래 검증 기준의 구현 결과와 PC 통합 미검증 범위는 개발 계획의 1단계 인계를 따른다.
- 동일 등록 재전송/동시 요청의 중복 방지, 같은 키의 다른 내용 거부, 실패 롤백 후 재시도와 DB 성공 뒤 응답 유실을 검증한다.
- 서버 재시작 뒤 기존 인물 보존·재로그인·후보 복원을 확인한다. 사용자 운영 DB와 backup/을 테스트에 사용하지 않는다.
- 멀티카메라·늦은 결과·STOP/재시작·동시 관측 충돌은 PC 통합 검증이며 OSNet 실측 정확도와 구분한다.


---

## 이벤트 API — PC 이벤트 MVP

### 구현 상태와 공통 규칙

2026-11-19 **2단계 이벤트 API 4개·저장소 설정 2개·기존 Access 인증 연결을 구현했다.** 임시 DB/설정/파일을 사용하는 검증과 실제 Uvicorn HTTP 검증을 수행했다. 3단계에서 PC 저장소 동기화와 공용 비동기 큐·카메라 시작/정상 중지 자동 전송을 연결했다. 4단계에서 인물 등장/자연 유실·미디어 갱신·종속 큐/종료도 연결했다. 5단계의 합성/단일 웹캠 정상 시나리오도 확인했다. [검증 기록](EVENT_MVP_STAGE5_VALIDATION.md)에 실제 수행 범위와 미검증을 구분한다. [이벤트 개발 계획](EVENT_MVP_DEVELOPMENT_PLAN.md)을 먼저 읽는다.

- 보호 API는 기존 관리자 Access 인증·공통 오류 JSON을 사용한다. PC/Flutter 권한 구분은 추가하지 않는다.
- 생성/미디어 갱신 요청은 알 수 없는 필드를 거부한다. ID는 양의 JSON 정수(bool/실수/문자열 거부), 세대는 0 이상 정수이며 SQLite 정수 상한 2^63−1을 적용한다. 시간은 시간대 포함 ISO 8601 입력·UTC 정규화·고정 소수 6자리 Z 응답이다.
- server_run_id와 event_id는 백엔드가 부여하며 생성 요청에서 거부한다. camera_execution_id는 UUID v4 문자열(기존 hex 입력 허용), 저장/응답은 표준 하이픈 문자열이다.
- DB 모델을 직접 응답하지 않고 EventResponse를 사용한다. 인증/입력 오류에 토큰·원본 요청·내부 DB 예외를 노출하지 않는다.
- 첫 이벤트 전 저장소 설정 필수이며 설정 없음은 409 STORAGE_ROOT_NOT_CONFIGURED. 현재 저장소 디렉터리와 파일 경계를 Service에서 검증한다. 이력 조회는 저장소가 없거나 일시적으로 이용 불가능해도 가능하다.

### 1. 생성 — POST /api/v1/events

EventCreate 요청:

| 필드 | 조건 |
| --- | --- |
| event_type | person_appeared / person_disappeared / camera_started / camera_stopped 중 하나, 필수 |
| camera_id | 등록 카메라 ID, 필수 |
| camera_execution_id | 현재 카메라 실행 UUID v4, 필수 |
| occurred_at | 실제 발생 시각, 필수 |
| tracking_generation | 인물 이벤트 필수, 카메라 이벤트는 생략/null |
| local_person_id | 인물 이벤트 필수, 카메라 이벤트는 생략/null |
| global_person_id | 인물 이벤트 필수, 카메라 이벤트는 생략/null |
| related_event_id | 사라짐만 필수, 나머지는 생략/null |
| crop_path / clip_path / trajectory_path | 등장만 값 허용, 생략 시 null |

```json
{
  "event_type": "person_appeared",
  "camera_id": 1,
  "camera_execution_id": "b20abf6d-b7fb-4323-b94b-581aebbd3c38",
  "tracking_generation": 0,
  "local_person_id": 3,
  "global_person_id": 12,
  "occurred_at": "2026-11-19T03:00:00.000000Z",
  "crop_path": "이벤트/person_12/2026-11-19/camera_1/12시00분00초/crop.jpg"
}
```

- 등장은 글로벌 ID 확정 후 저장하되 시각은 첫 탐지다. 사라짐은 정상 수신 중 약 3초 미탐지, 시각은 마지막 탐지다. 시작은 첫 프레임, 중지는 정상 정리 완료 시각이다. 발생 판단은 PC 책임이며 백엔드는 프레임/AI를 처리하지 않는다.
- 사라짐 대상은 같은 서버 실행/카메라/카메라 UUID/세대/로컬 ID/글로벌 ID의 등장이어야 하며 시각은 등장 이상이다. STOP·끊김·초기화는 사라짐을 만들지 않는다.
- 인물 중복 키는 (server_run_id, camera_id, camera_execution_id, tracking_generation, local_person_id, event_type), 카메라 중복 키는 (server_run_id, camera_id, camera_execution_id, event_type)다.
- 신규 커밋 201, 같은 키·같은 불변 내용 재전송 200과 같은 event_id/현재 DTO. 불변 내용이 다르면 409 EVENT_REQUEST_CONFLICT. 생성 재요청의 미디어 경로는 반영하지 않고 현재 상태를 반환하며 경로 변경은 PATCH만 사용한다. 추가 request_id나 테이블은 없다.
- 응답 유실은 같은 키로 재확인한다. PC 생성 요청의 옛 미디어 경로가 폴더 rename 경합으로 거부되면 같은 불변 식별값/시각으로 미디어 없는 생성 재확인을 한 번만 하고 최신 경로를 PATCH한다. 정상 생성에는 준비된 경로를 함께 보낸다. 재로그인/새 서버 실행 뒤 과거 미확정 요청 자동 재전송은 PC에서 금지한다.
- EventResponse: 위 요청 필드 전체(생략 필드도 null로 포함) + event_id + server_run_id + cnn_result. cnn_result는 초기 null이며 아래 후속 CNN API로 갱신한다. 생성 요청에서는 cnn_result를 거부한다. DB에 저장한 상대 경로만 응답하고 서버 저장소 절대 경로는 이벤트 응답에 넣지 않는다.

### 2. 미디어 — PATCH /api/v1/events/{event_id}/media

- EventMediaUpdate: crop_path, clip_path, trajectory_path 중 하나 이상만 전달한다. 빈 객체/추가 필드 거부. 등장 외 이벤트는 409 EVENT_MEDIA_NOT_ALLOWED.
- 생략은 유지, 명시 null은 제거. 세 경로를 한 요청으로 보내면 한 트랜잭션으로 갱신한다. 반복한 같은 PATCH는 같은 결과로 200이다.
- 파일 작성 완료 → 사용자 저장 위치 이동 성공 → DB 갱신 순서. temp/아직 작성 중 파일 경로는 허용하지 않는다. 추적 종료로 폴더명 확정 시 세 경로의 최신 상태를 한 PATCH로 보낸다.
- 경로는 AI_CCTV 기준 / 구분자 상대 경로다. 절대/드라이브/UNC/역슬래시/상위 이동/빈 항목/제어 문자/Windows 예약 문자를 거부한다. 파일은 기존 이벤트 저장 구조의 정확한 6개 경로 항목(이벤트/person_ID/YYYY-MM-DD/camera_ID/관측폴더/파일명)을 사용한다. 등장 행의 인물/카메라와 경로가 일치하고 파일명은 각 필드에 맞는 crop.jpg/clip.mp4/trajectory.jpg여야 한다. 기준 경로 아래 기존의 0바이트가 아닌 일반 파일만 허용하고 하위 심볼릭 링크·Windows junction/reparse point는 내부 목적지여도 거부한다. 갱신 후 남는 미디어 경로들은 모두 같은 관측 폴더여야 한다.
- 순서 버전 컬럼은 추가하지 않는다. PC는 관측별로 한 요청만 진행하고 알림 revision을 비교해 최신 경로를 합친다. 자동 PATCH에는 이용 가능한 경로만 보내며 아직 없는 필드는 생략하므로 옛 요청이 나중에 완료되어도 새 파일 경로를 NULL로 지우지 않는다. 폴더명 변경 시 이용 가능한 모든 최신 경로를 한 요청으로 보낸다. 관리 API의 명시 null 제거 계약은 유지한다.
- 성공 200 + 현재 EventResponse. 사라짐에는 미디어를 복제하지 않는다.

### 3. 목록 — GET /api/v1/events

- 쿼리: limit 기본 50, 1~100. before_occurred_at과 before_event_id는 둘 다 생략하거나 둘 다 전달한다. HTTP 쿼리 숫자는 0~9 문자만 받아 범위 검증 후 정수로 변환한다. 빈 값·부호·공백·소수·boolean 표기·알 수 없는 쿼리는 거부한다.
- occurred_at DESC, event_id DESC. 커서는 마지막 행의 두 값을 전달하며 (occurred_at, event_id)가 그보다 작은 행만 반환한다. 커서 시각은 UTC로 정규화한다.
- 성공 200 `{"items": [EventResponse, ...]}`. 빈 결과는 빈 배열이다. 다음 페이지는 마지막 행을 커서로 조회하며 결과가 비면 종료한다. total/복잡한 검색·통계·구독은 없다.

### 4. 상세 — GET /api/v1/events/{event_id}

성공 200 + EventResponse, 부재 404 EVENT_NOT_FOUND. 과거 서버 실행의 이력도 조회한다. 이미지/영상 파일 응답·정적 마운트·HTTP Range는 구현하지 않는다.

### 5. 저장소 설정 — 2단계 구현 완료

- GET /api/v1/settings/storage: Access 인증, 200 `{"storage_root": null 또는 절대경로, "locked": boolean}`. storage_root는 선택 위치 자체가 아니라 최상위 AI_CCTV 폴더다. 관리자용 설정 응답이며 이벤트 DTO에는 절대 경로를 넣지 않는다.
- PUT /api/v1/settings/storage: Access 인증, `{"storage_root": "C:/선택위치/AI_CCTV"}`. 성공 200과 같은 설정 DTO. null/상대 경로/알 수 없는 필드 거부. 이미 존재하고 이름이 AI_CCTV인 프로젝트와 같은 볼륨의 폴더만 지정한다. 실제 경로로 정규화하며 프로젝트 temp/output 하위는 거부한다. 미디어 폴더 생성·기존 파일 이동·삭제는 수행하지 않는다.
- 기본 설정 파일은 DB 옆의 `ai_cctv.storage.json`(`client_code/backend/data/`)이며 `{"storage_root":"절대경로"}` 형식으로 재실행에도 유지한다. 임시 파일 flush/fsync·닫기 후 같은 디렉터리에서 os.replace로 교체한다. 설정 파일 위치는 실행 디렉터리에 독립적이며 create_app(db_path, storage_config_path)로 검증 경로를 주입할 수 있다. 각 DB는 자기 설정 파일을 사용한다. 새 설정 테이블 없음.
- 첫 이벤트 커밋 이후 다른 기준 경로는 409 STORAGE_ROOT_LOCKED. 동일 정규 경로는 허용한다. 제한은 이벤트가 카메라 종류여도 적용되며 재실행 뒤에도 유지한다.
- 설정 변경과 이벤트 생성/미디어 갱신은 단일 프로세스의 공용 잠금과 SQLite BEGIN IMMEDIATE 순서로 조정한다. 첫 이벤트 커밋 전 검증 실패/롤백은 경로를 잠그지 않는다. 설정 교체 실패는 이전 파일과 메모리 경로를 유지한다. 3단계 GUI는 로그인/설정 화면에서 설정을 조회하고, PUT 확인 후 설정 창을 닫으며 확인된 서버 경로를 Worker에 전달한다. 미설정/설정 요청 중 START 및 촬영/정리 중 설정 변경을 차단한다. 이벤트 이력이 있으면 위치 선택도 비활성화한다.

### 6. CNN 결과 — PATCH /api/v1/events/{event_id}/cnn-result

2026-11-20 CNN 2단계 구현. 기존 관리자 Access 인증과 공통 오류를 사용한다. API 요청에서 모델 추론은 실행하지 않는다. 후속 3단계에서 PC 공용 CNNWorker/자동 제출을 연결했고 실제 단일 웹캠 정상 흐름을 검증했다. 상세 검증 범위는 [CNN 개발 계획](CNN_APPEARANCE_DEVELOPMENT_PLAN.md)을 따른다.

요청 예시(실제 추론 결과가 아닌 계약 예시):

```json
{
  "cnn_result": {
    "model_version": "exp3",
    "attributes": {
      "gender": "Male",
      "age": "청년",
      "hat": "No Hat",
      "upper_color": ["Black", "White"],
      "lower_color": ["Blue"],
      "sleeve": "Short Sleeve",
      "lower_type": "Long Pants",
      "carrying": []
    }
  }
}
```

- cnn_result와 8개 속성 전체가 필수다. 빈 객체/null/추가 필드/지원하지 않는 라벨은 422다. model_version은 exp3만 받는다.
- gender: Male/Female. age: 소년/소녀/청년/장년. 소년은 Male, 소녀는 Female과 일치해야 한다. 원래 5개 나이 클래스는 PC 후처리 후 보내며 API에서 재변환하지 않는다.
- hat: No Hat/Hat. sleeve: Long Sleeve/Short Sleeve/No Sleeve. lower_type: Long Pants/Short Pants/Skirt.
- upper_color 1~11개, lower_color 1개: Black/Blue/Brown/Green/Grey/Orange/Pink/Purple/Red/White/Yellow.
- carrying 0~5개: Backpack/MessengerBag/PlasticBag/Luggage/Other. 빈 배열은 소지품 검출 없음이다. 모든 배열은 중복 라벨을 거부한다.
- 등장만 갱신 가능하며 다른 이벤트는 409 EVENT_CNN_RESULT_NOT_ALLOWED, 부재는 404 EVENT_NOT_FOUND다. 과거 서버 실행의 등장도 기존 Access로 갱신 가능하다.
- 결과 객체 전체 교체다. 같은 요청 재전송은 동일 결과를 반환하고 다른 유효 결과는 마지막 커밋이 반영된다. 결과 삭제(null)는 제공하지 않는다. 미디어 PATCH/생성 재전송은 CNN 결과를 유지한다.
- 성공 200 + 현재 EventResponse, Cache-Control: no-store. 식별값/발생 시각/미디어/행 개수는 바꾸지 않는다. 기존 목록·상세 등 EventResponse에 cnn_result가 포함된다.
- 저장소 파일을 재검사하지 않으므로 미디어 경로/저장소가 일시적으로 없어도 결과 저장은 가능하다. NULL은 대기/미분석/실패를 구분하지 않는다.

### 오류 계약

| HTTP | 코드 | 조건 |
| --- | --- | --- |
| 401 | INVALID_ACCESS_TOKEN | 기존 Access 인증 실패 |
| 404 | EVENT_NOT_FOUND / CAMERA_NOT_FOUND / GLOBAL_PERSON_NOT_FOUND | 관련 리소스 부재 |
| 409 | EVENT_REQUEST_CONFLICT | 같은 키의 다른 불변 내용 |
| 409 | EVENT_RELATION_CONFLICT | 관련 등장 종류/관측/시각 불일치 |
| 409 | EVENT_MEDIA_NOT_ALLOWED | 등장 이외 미디어 갱신 |
| 409 | EVENT_CNN_RESULT_NOT_ALLOWED | 등장 이외 CNN 결과 갱신 |
| 409 | EVENT_RUN_CONFLICT | Service의 현재 서버 실행이 없거나 running이 아님 |
| 409 | STORAGE_ROOT_NOT_CONFIGURED / STORAGE_ROOT_LOCKED | 설정 없음/변경 제한 |
| 422 | VALIDATION_ERROR | 종류별 입력/시각/UUID/경로/커서/범위 오류 |
| 500 | INTERNAL_SERVER_ERROR | 예상하지 못한 DB/설정 저장 오류 |

모든 성공 응답에 Cache-Control: no-store를 적용한다. POST는 Location 헤더도 반환한다. 모델/Service 및 TestClient 검증과 실제 Uvicorn HTTP 검증 결과는 이벤트 개발 계획을 따른다.

### 저장소 장애와 검증 한계

- 설정 파일이 손상되면 기존 내용을 초기화하지 않고 백엔드 시작을 실패시킨다. DB에 이벤트가 있는데 설정 파일이 사라지면 locked=true/storage_root=null이며 새 설정/이벤트 쓰기를 거부한다. 기존 설정 파일을 복원해야 하며 자동으로 다른 저장소를 선택하지 않는다.
- 설정된 폴더가 일시적으로 없으면 이력/설정 GET은 유지하고 새 이벤트/미디어 쓰기는 422다. 같은 기준 경로의 복구 후 재사용하며 다른 루트로 전환하지 않는다.
- 일반 파일 존재·비어 있지 않음·경계만 확인한다. 디코딩·Writer 종료 여부·촬영 관측과 실제 파일 내용의 관계는 백엔드에서 증명하지 않으며 PC가 완료/이동 순서를 지켜야 한다. 외부 프로세스가 검증 직후 파일을 삭제/교체하는 경우까지 원자적으로 보호하지 않는다.
- DB와 파일은 하나의 트랜잭션이 아니다. 파일 이동 후 DB 갱신 실패 시 파일을 되돌리거나 삭제하지 않는다. 3단계 PC 저장 큐/GUI·로그아웃 연결은 구현했고 임시 HTTP/합성 입력으로 검증했다. 4단계 인물/미디어 자동 전송도 연결하고 임시 HTTP/실제 생성 파일로 검증했다. 5단계에서 실제 단일 웹캠/모델의 정상 흐름도 확인했다. 두 카메라/RTSP/장시간 범위는 검증 기록의 제한을 따른다.
