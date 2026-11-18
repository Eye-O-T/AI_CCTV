# PC CCTV 백엔드

현재 SQLite 기반 최초 가입 필요 여부 조회·최초 회원가입·로그인·로그아웃·토큰 갱신과 `GET /health`가 구현되어 있다. `users`, `server_runs`와 카메라 등록용 `cameras` 모델이 등록되어 서버 시작 시 없는 테이블을 생성한다. Camera 모델·스키마·Repository·Service·Router와 카메라 등록·목록·상세·부분 수정 API를 구현했다. PyQt 인증 화면과 서버 자동 시작·종료는 연결했으며 카메라 PyQt 관리 화면과 단일 카메라 런타임을 연결했다. 이벤트 API는 미구현이다. 클라이언트 실행·연동 검증은 [클라이언트 README](../README.md)를 참고한다.

```text
backend/
├─ __init__.py
├─ main.py           # FastAPI 앱 생성·라우터 등록·시작/종료
├─ database.py       # SQLite 연결·DB 세션
├─ dependencies.py   # DB 세션·현재 사용자 의존성
├─ security.py       # 비밀번호 해시·토큰 생성/검증
├─ errors.py         # 공통 API 오류
├─ routers/          # Controller: HTTP 요청·응답
├─ services/         # 업무 규칙·트랜잭션 경계
├─ repositories/     # DB 접근
├─ models/           # Entity: SQLAlchemy 테이블 모델
└─ schemas/          # DTO: Pydantic 요청·응답 모델
```

## 구현 기준

- [백엔드 스키마 및 제약사항](../docs/BACKEND_SCHEMA.md)을 참고한다.
- 2026-11-18 설계 갱신: 스키마 문서에 global_person_manage를 포함한 5개 테이블 설계를, [통합 API 명세서](../docs/API_SPEC.md#인물-이벤트글로벌-인물-api)에 인물 이벤트 생성·대표 벡터 조회·매칭 저장 계약을 추가했다. 이벤트·글로벌 인물·OSNet 작업을 구현할 때 읽는다. 현재 등록 모델은 여전히 users/server_runs/cameras 3개이며, 새 DB를 실행해도 나머지 2개는 자동 생성되지 않는다. 이 문서 갱신은 기존 DB의 변경·초기화·마이그레이션을 수행하지 않는다.
- API 구현·클라이언트 연동·검증 전에는 [통합 API 명세서](../docs/API_SPEC.md)를 읽는다. 인증·카메라 관리 API 계약과 구현이 정리되어 있으며, 하단의 인물 이벤트·글로벌 인물 계약은 구현 예정이다. PyQt 목록·등록·수정 및 선택 카메라 실행을 연결했다. 실제 장치 검증 범위는 클라이언트 README에 구분한다.
- 요청 처리는 Router → Service → Repository 순서로 나눈다.
- DB 모델과 API 요청·응답 모델을 분리한다.
- 백엔드는 PyQt 화면이나 YOLO·CNN 모델을 import하지 않는다.
- 관제 프로그램은 HTTP로 백엔드에 접근하며 Repository를 직접 호출하지 않는다.
- 백엔드는 별도 프로세스로 실행한다. 관제 프로그램은 자신이 생성한 서버 프로세스의 시작·종료를 관리하며, 아래 명령으로 API만 단독 실행할 수도 있다.
- Refresh 토큰을 메모리에서 관리하므로 초기 백엔드는 단일 프로세스를 전제로 한다.
- 실제 기능과 추가 파일은 사용자가 요청한 개발 단계에서 구현한다.

## 실행

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -m uvicorn client_code.backend.main:app --host 127.0.0.1 --port 8000
```

- 상태 확인: http://127.0.0.1:8000/health
- Swagger 문서: http://127.0.0.1:8000/docs
- OpenAPI: http://127.0.0.1:8000/openapi.json
- 종료: 실행 터미널에서 `Ctrl+C`.
- 단일 프로세스·단일 인스턴스로 실행한다. 여러 worker를 사용하지 않는다. 동일 DB 중복 실행은 파일 잠금으로 거부한다.
- 기존 서버가 8000 포트를 사용한다면 기존 서버의 터미널에서 정상 종료 후 새 코드를 실행한다. 다른 프로세스를 임의로 종료하지 않는다.
- DB는 실행 위치에 관계없이 `client_code/backend/data/ai_cctv.db`에 생성된다. 계정과 실행 이력을 보존하며 이 디렉터리는 Git에서 제외한다. DB 파일 삭제는 관리자 계정 삭제이므로 테스트 초기화 목적으로 무작정 삭제하지 않는다.
- `create_all`은 없는 테이블만 생성한다. 기존 테이블 구조를 자동 마이그레이션하지 않는다.
- 서버 시작 시 실행 기록을 만들고, 이전 `running` 기록은 중복 실행이 아닌 것을 확인한 뒤 `interrupted`로 변경한다. 알 수 없는 강제 종료 시각은 채우지 않는다. 정상 종료는 `stopped`와 종료 시각을 기록한다.
- JWT 서명 키는 시작마다 메모리에서 무작위 생성한다. 서버 재시작·개발 리로드 시 기존 토큰이 모두 무효화된다.
- 최초 가입 전 서버를 로컬 주소에 바인딩하여 사용한다. 외부 접근·HTTPS 배포는 이번 구현 범위가 아니다.

### 의존성

기존 가상환경을 재사용한다. 루트 `requirements.txt`에 인증용 SQLAlchemy·PyJWT·argon2-cffi를 추가했다. 해당 패키지가 없는 환경에서만 다음 명령으로 추가한다. 전체 AI 패키지를 재설치하거나 업그레이드할 필요는 없다.

```powershell
.\venv311\Scripts\python.exe -m pip install "SQLAlchemy>=2.0,<2.1" "PyJWT>=2.10,<3" "argon2-cffi>=25.1,<26"
```

검증 환경: Python 3.11.9, FastAPI 0.138.0, Pydantic 2.13.4, Uvicorn 0.49.0, SQLAlchemy 2.0.54, PyJWT 2.15.1, argon2-cffi 25.1.0.

## Postman 확인 순서

기본 URL: `http://127.0.0.1:8000`. POST 요청의 Body는 `raw / JSON`으로 설정한다. 다섯 API는 Access 인증 헤더가 필수가 아니다. 요청·응답 전체 계약은 [통합 API 명세서](../docs/API_SPEC.md)를 따른다.

| 순서 | 요청 | 확인 |
| --- | --- | --- |
| 0 | GET `/api/v1/auth/signup-status` (본문 없음) | 관리자 없으면 signup_required=true, 있으면 false |
| 1 | POST `/api/v1/auth/signup` | 최초 201, 재요청 409 |
| 2 | POST `/api/v1/auth/login` | 200, Access·Refresh 두 토큰 발급 |
| 3 | POST `/api/v1/auth/refresh` | 로그인 응답의 Refresh 전달, 새 두 토큰 저장 |
| 4 | 동일한 이전 Refresh로 다시 갱신 | 401 |
| 5 | POST `/api/v1/auth/logout` | 3번에서 받은 최신 Refresh 전달, 본문 없는 204 |
| 6 | 로그아웃한 Refresh로 갱신 | 401 |
| 7 | 다시 로그인 | 아이디·비밀번호로 새 세션 발급 |

회원가입 예시(로컬 테스트용 값):

```json
{"username": "admin", "password": "test-pass", "password_confirm": "test-pass"}
```

로그인:

```json
{"username": "admin", "password": "test-pass"}
```

갱신·로그아웃:

```json
{"refresh_token": "<현재 Refresh 토큰>"}
```

로그아웃과 갱신을 동시에 보내지 않는다. 보호 API용 `require_user_id` 의존성은 구현되어 있으나 테스트 목적으로 제품에 별도 `/me` 등의 경로를 추가하지 않았다. Access 토큰 폐기는 테스트 전용 라우트에서 확인한다. `/health`는 공개 경로이므로 로그아웃 이후에도 200을 반환한다.

## 자동 검증

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/backend/tests -v
.\venv311\Scripts\python.exe -m pip check
```

- 테스트는 `tests/test_auth.py`에 있으며 임시 DB를 사용한다. 실제 관리자 계정을 만들거나 운영 DB를 초기화하지 않는다.
- 테스트 의존성은 `tests/requirements.txt`에 선언했다. 현재 환경의 httpx 0.28.1을 재사용했다.
- 최초 가입 필요 여부 조회는 임시 DB에서 가입 전·후 및 서버 재시작 후 응답, 캐시 금지, DB 오류를 가입 필요로 오인하지 않는 동작을 검증한다. 기존 인증 회귀 검증과 함께 총 18개 테스트를 사용한다.
- 실제 로컬 Uvicorn HTTP로 `/health`, `/docs` HTML, `/openapi.json`, 가입→로그인→갱신→로그아웃→갱신 거부를 확인했다. 브라우저 Swagger 화면 조작이나 Postman 직접 조작, PyQt·카메라 실행을 검증한 것은 아니다.
- `pip check` 통과. 기존 Starlette는 테스트의 httpx 사용에 폐기 예정 경고를 내지만 테스트는 통과한다. 이 경고를 없애기 위한 환경 업그레이드는 하지 않았다.

## 남은 범위

- 입력 최대 길이·아이디 문자 정책은 미확정이므로 최소 4자만 적용한다. 아이디는 대소문자를 구분하며 입력을 trim·정규화하지 않는다.
- 비밀번호 변경, 이벤트 기능, Flutter용 영상 송출은 미구현이다. 카메라 관리 API까지 구현·검증했다.
- PyQt는 토큰을 메모리에만 보관하며 영상 작업 종료 후 로그아웃한다. 실제 카메라·GPU 작업 종료 검증은 클라이언트 README의 제한 사항을 참고한다.


## 카메라 모델·스키마 구현 (2026-10-07)

- `models/camera.py`: 카메라 등록 모델, 종류별 CHECK, 소스 UNIQUE, UTC 시각과 사용 여부 기본값을 구현했다. 다음 서버 시작에서 기존 `create_all`이 없는 cameras 테이블을 생성한다. 운영 DB를 직접 열거나 초기화하지 않았다.
- `schemas/camera.py`: CameraCreate, CameraUpdate, CameraSummary, CameraResponse, CameraListResponse를 구현했다. 입력 타입·주소·종류 조합·읽기 전용 필드를 검증하고, 목록에서는 접속 주소를 제외한다.
- PATCH는 `model_dump(exclude_unset=True)`로 전달된 필드만 추출한다. Service는 기존 등록 정보에 병합한 값을 `CameraCreate.model_validate(...)`로 검증한다. 종류 변경 금지는 CameraUpdate의 필드 제한이며 직접 SQL 변경까지 차단하는 트리거는 추가하지 않았다.
- 이름·주소 정리는 스키마에서 수행한다. DB는 필수값·종류 조합·이름 길이·장치 번호·사용 여부·중복을 보장하며 URL 형식 검증은 DTO 책임이다.
- 오류 문자열에서 원본 입력을 숨기지만 Pydantic의 `ValidationError.errors()`에는 기본적으로 input이 포함될 수 있다. Router의 공통 오류 처리와 Service 검증 오류 변환은 원본 입력을 응답에 포함하지 않는다. 원본 오류 객체를 로그에 기록하지 않는다.
- `tests/test_camera.py`는 메모리 SQLite로 스키마 입력 경계, PATCH 생략/null, URL 보존·거부, DB CHECK·UNIQUE, UTC 시각, 목록/상세 응답을 검증한다. API 검증은 아래 절을 참고한다. PyQt·카메라 실행 검증은 후속 범위다.
- 모델·스키마 테스트 9개는 아래 API·인증 검증과 함께 통과했다. 기존 Starlette/httpx 폐기 예정 경고는 유지된다.


## 카메라 API 구현·검증 (2026-10-07)

- `repositories/camera_repository.py`: 등록·조회·수정과 flush만 담당하며 commit하지 않는다.
- `services/camera_service.py`: 트랜잭션, PATCH 최종 조합 검증, 404·409·422 오류 변환을 담당한다. SQLite `BEGIN IMMEDIATE`를 조회 전에 실행하여 동시 PATCH가 이전 값을 덮어쓰지 않게 한다. 소스 UNIQUE 위반만 409로 처리하며 다른 DB 오류는 공통 500으로 처리한다.
- `routers/cameras.py`: 기존 Access 인증을 적용한 POST/GET `/api/v1/cameras`, GET/PATCH `/api/v1/cameras/{camera_id}`를 제공한다. 등록 성공 시 Location 헤더를 반환한다. 목록은 ID 오름차순이며 `is_enabled=true/false`로 필터링한다.
- 수정 시 생략한 필드는 유지하고, 명시적 null은 최종 카메라 종류에 맞게 검증한다. 동일한 값의 수정은 updated_at을 유지한다. 등록·수정 과정에서 실제 장치 접속은 하지 않는다.
- 자동 검증: 기존 인증 18개, 모델·스키마 9개, 신규 카메라 API·수명주기 11개로 총 38개 통과. 정상 흐름, 인증 누락·만료·로그아웃·Refresh 오용, 입력 실패, 중복, 동시 요청, flush 이후 실패 롤백, 재시작 후 등록 유지·재로그인을 확인했다.
- 실제 Uvicorn을 임시 DB·127.0.0.1 임시 포트로 실행하여 HTTP 등록·목록·상세·수정, 404/409/422, OpenAPI 경로를 검증했다. 운영 DB·장치에 접근하지 않았으며 Swagger 화면 클릭·Postman 직접 조작·PyQt·실제 카메라 수신·Flutter 재생 검증은 아니다.

### 직접 확인 순서

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`. 서버 실행 명령은 위 실행 절과 동일하다. 실행하면 기본 DB에 없는 테이블이 생성되며, 아래 등록 요청은 해당 DB에 저장되므로 실제 등록할 정보로 입력한다.

1. 로그인 API에서 Access 토큰을 발급받는다.
2. Swagger의 Authorize에 Access 토큰을 입력하거나 요청에 `Authorization: Bearer <access_token>` 헤더를 설정한다.
3. POST `/api/v1/cameras`에 `{"name":"정문","camera_type":"webcam","webcam_index":0}`를 전달한다. 기존 등록과 중복이면 409다.
4. GET `/api/v1/cameras` 및 반환된 Location 경로로 목록·상세를 확인한다.
5. PATCH 해당 Location에 `{"is_enabled":false}`를 보내고 GET `/api/v1/cameras?is_enabled=false`로 확인한다.
6. 다시 사용할 카메라는 PATCH에 `{"is_enabled":true}`를 전달한다. 서버 재시작 후에는 재로그인하고 등록 정보가 유지되는지 확인한다.

PyQt 관리 화면과 선택 카메라 런타임 연결을 완료했다. 실제 장치 검증은 클라이언트 README를 참고한다. 영상 송출 API·이벤트 기능은 이번 범위에서 구현하지 않았다.
