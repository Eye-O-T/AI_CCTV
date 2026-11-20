# PC CCTV 백엔드

## 모바일 인물 이벤트 상세 1단계

- `GET /api/v1/mobile/events/{event_id}`를 추가했다. 기존 Access 인증으로 인물 등장/사라짐의 시각·타입·글로벌 ID·현재 카메라 이름·CNN 전체 결과와 crop/궤적/클립 URL을 조회한다. 미등록 결과/경로는 각각 null이며 재조회하면 최신 값이 반영된다.
- 사라짐 자체의 ID/타입/시각을 유지하고 표시 데이터는 연결된 등장으로부터 읽는다. 카메라 시작/중지는 409 EVENT_DETAIL_NOT_SUPPORTED, 이벤트 없음은 404 EVENT_NOT_FOUND다.
- 기존 목록/crop/이벤트 API·DB·탐지/저장·사용자가 변경한 서버 바인딩을 유지했다. 이번 단계는 JSON만 구현했으며 **궤적/클립 파일 GET은 아직 미구현**이다. 기존 crop_url만 이미지 요청이 가능하다.
- 상세 작업/연동 전에 [API 명세의 모바일 상세 절](../docs/API_SPEC.md#모바일-인물-이벤트-상세-api--1단계)을 읽는다. 상세 자동 검증은 `tests/test_mobile_event_detail_api.py`에 있다. Postman 컬렉션 확장은 계획상 상세 4단계이며 기존 목록 컬렉션은 유지했다.
- 다음은 상세 2단계 궤적 이미지 GET, 3단계 클립 GET/Range, 4단계 회귀/문서/Postman 인계, 5단계 사용자 확인 순서다. 영상·궤적 표시가 이번 단계에서 완료된 것은 아니다.
- 검증: 신규 상세 6개(TestClient 5개 + 실제 로컬 Uvicorn HTTP 1개)를 포함해 백엔드 전체 **120개 통과**(58.563초). 등장/사라짐 자체 시각·타입, CNN 전체/미디어 갱신·null, 현재 카메라 이름, 저장소 장애 중 JSON 조회, 인증/404/409/422, 기존 이벤트 DTO 보존을 확인했다. AST 6개·OpenAPI·git diff --check도 통과했다. 운영 DB/계정/미디어·실제 카메라·Postman 직접 실행·Flutter는 검증하지 않았다. 기존 가용 Python 3.11 + venv311 site-packages를 재사용했으며 패키지를 변경하지 않았다.

## 모바일 API 3단계: 회귀 확인·Postman 인계 (2026-11-21)

1·2단계의 제품 코드는 유지하며 기존 API 회귀 확인과 테스트 자료를 정리했다. 모바일 API 수동 확인 전에 [API 명세](../docs/API_SPEC.md#모바일-이벤트-목록-api--2026-11-21-12단계)와 이 절을 읽는다. 이번 단계는 Flutter·상세/영상/Live·서버 바인딩·방화벽을 변경하지 않는다.

### Postman 준비 및 요청 순서

1. 관제 프로그램을 정상 종료 후 다시 실행한다. Postman은 같은 PC에서 실행하며 base_url은 `http://127.0.0.1:9000`이다. 같은 DB로 별도 백엔드를 중복 실행하지 않는다.
2. [Postman 컬렉션 JSON](postman/mobile-events.postman_collection.json)을 Import한다. 컬렉션 Variables의 username/password를 기존 계정으로, date를 기록이 있는 한국 날짜로 지정한다. limit 기본값은 30이며 페이지 확인 시 2처럼 줄일 수 있다.
3. `01 로그인`을 Send하고 응답 access_token을 컬렉션 access_token 변수에 직접 복사한다. 나머지 요청은 컬렉션 Bearer 인증을 상속한다. 토큰 자동 저장 스크립트는 없다.
4. `02 날짜별 목록`을 Send한다. crop_url이 있는 카드의 event_id를 변수에 복사하고 `03 crop 이미지`를 Send한다. Body Preview에서 이미지를 확인한다. 사라짐 카드 ID로도 반복한다.
5. next_cursor가 있으면 두 값을 before_occurred_at/before_event_id 변수에 복사해 `04 다음 페이지`를 Send한다. null이면 생략한다. 같은 date/limit을 유지하고 커서의 UTC Z 문자열을 그대로 사용한다.
6. `05 기존 이벤트 상세`로 기존 DTO를 확인한다. `06 잘못된 날짜`는 422, `07 인증 없는 목록`은 401이 정상이다. 컬렉션은 개별 Send용이며 일괄 Runner용이 아니다.

### 확인 항목

| 확인 | 기대 결과 |
| --- | --- |
| 로그인 | 200, Access/Refresh 발급. 관제 프로그램 로그인 유지 |
| 날짜 목록 | 선택한 한국 날짜, 최신순 카드, items/next_cursor |
| 인물 등장 | 글로벌 ID·현재 카메라 이름·UTC 시각·crop_url |
| 인물 사라짐 | 자체 ID/타입/시각, 연결된 등장 이미지·분석 정보 |
| 카메라 시작/중지 | 타입·카메라·시각, 인물/이미지/성별/나이는 null |
| CNN 새로고침 | 분석 전 null, 실제 분석 저장 후 첫 페이지 재조회 시 결과 표시 |
| 이미지 | 인증 포함 GET으로 200 image/jpeg, Cache-Control: no-store |
| 페이지 | 중복 없이 이어짐, 마지막 next_cursor=null |
| 기존 API | 기존 목록/상세 DTO 유지. 사라짐 원본 미디어/CNN은 null |
| 오류 | 잘못된 날짜 422, 인증 누락/만료 401, 없는 이벤트 404 EVENT_NOT_FOUND, crop 없음 404 EVENT_CROP_NOT_FOUND |

기록 없는 날짜는 date만 바꾸고 커서를 초기화해 items=[]를 확인한다. 존재하지 않는 이벤트 ID로 crop GET을 요청하면 EVENT_NOT_FOUND다. 실제 데이터에 해당 종류나 분석 전 상태가 없으면 미확인으로 남기며 운영 DB를 편집하거나 파일을 지워서 오류를 만들지 않는다. 새로고침/날짜 변경 시 커서를 초기화한다.

Access 토큰은 최대 15분 유효하고 서버 재시작으로도 무효화된다. 401이면 다시 로그인해 변수를 바꾼다. 테스트 후 비밀번호/토큰 값을 지우며 실제 값이 들어간 컬렉션·응답은 공유하지 않는다. Postman 결과는 요청 이름·선택 날짜·HTTP 상태·기대 결과 일치 여부로 남기면 된다. 위 항목은 절차이며 사용자의 Postman 수행 완료를 뜻하지 않는다.

### 3단계 자동 검증 결과

- 기준 체크아웃: dev, f9c0fd03. 제품 코드 변경 없이 임시 DB/파일로 백엔드 전체 **114개 통과**(59.703초). 기존 인증·카메라·글로벌 인물·이벤트 API 및 모바일 목록/crop을 포함한다.
- 실제 로컬 Uvicorn HTTP로 목록→CNN 재조회→crop JPEG→사라짐 이미지→파일 소실 404 흐름을 확인했다. 이 결과는 Postman 직접 조작·운영 DB·실제 카메라·Flutter·다른 기기 접속 검증을 뜻하지 않는다.
- 기존 venv 실행기 대신 가용 python3.11.exe에 venv311/Lib/site-packages를 PYTHONPATH로 연결해 실행했다. 패키지는 변경하지 않았다. 기존 Starlette/httpx 폐기 예정 경고는 유지된다.
- 컬렉션 JSON 구문·7개 요청의 경로/메서드와 실제 OpenAPI 일치·계정/토큰 기본값 공백·인증 없는 오류 요청을 정적으로 확인했다. git diff --check도 통과했다. Postman Import/Send 직접 실행 결과는 아니다.
- 다음 단계는 사용자의 Postman API 확인이다. 서버 바인딩은 현재 127.0.0.1:9000 그대로이며 같은 PC에서만 확인한다.

## 모바일 crop 이미지 2단계 (2026-11-21)

- `GET /api/v1/mobile/events/{event_id}/crop`을 추가했다. 목록 crop_url에 기존 Access Bearer 인증으로 GET하면 원본 image/jpeg를 반환한다. 등장/사라짐 모두 지원하며 사라짐은 연결된 등장 파일을 사용한다.
- 기존 StorageService 경로 검증을 재사용한다. 저장소 밖 경로/다른 인물·카메라 경로/링크·junction을 거부하며 파일 없음·빈 파일·접근 실패는 404 EVENT_CROP_NOT_FOUND다. 이벤트 자체가 없으면 404 EVENT_NOT_FOUND다. 성공은 no-store/nosniff이며 내부 절대경로는 응답하지 않는다.
- 기존 이벤트 API/DB/PC 저장 코드는 유지한다. 이미지를 바로 읽는 단순 MVP이며 JPEG 재인코딩·영상·상세·Live·외부 접속 설정은 추가하지 않았다. 파일 검증 직후 외부 프로세스의 교체까지 원자적으로 보호하지는 않는다.
- 모바일 목록/이미지 작업·Postman 검증 전 [API 명세](../docs/API_SPEC.md#모바일-이벤트-목록-api--2026-11-21-12단계)를 읽는다. Postman에서는 crop GET에도 목록과 같은 Bearer Token을 설정한다. 다음 작업은 사용자의 Postman 확인이며 앱·다른 기기 연결은 후속 범위다.
- 검증 결과: 모바일 목록/crop 15개를 포함한 백엔드 전체 **114개 통과**(51.895초). 실제 JPEG 반환·사라짐 연결·인증 누락/만료/로그아웃·파일 없음/빈 파일/디렉터리·저장소 장애·경로 위조·Windows junction·파일 읽기 실패·폴더 변경 후 동일 URL을 확인했다. 실제 로컬 Uvicorn HTTP에서도 목록→crop/사라짐 이미지/파일 소실 404를 검증했다. AST 6개와 git diff --check도 통과했다. 운영 DB/카메라/Postman 직접 조작/Flutter는 검증하지 않았다. 기존 Python 실행 환경을 재사용했고 패키지를 변경하지 않았다.

## 모바일 이벤트 목록 1단계 (2026-11-21)

- `GET /api/v1/mobile/events`를 추가했다. 기존 관리자 Access 인증, 필수 한국 날짜 `date=YYYY-MM-DD`, limit 기본 30/최대 100, 발생 시각+ID 커서 페이지 조회를 사용한다.
- 등장/사라짐 카드의 crop 주소·추정 성별/나이·현재 카메라 이름을 한 조회로 결합한다. 사라짐의 이미지/분석은 연결된 등장에서 가져온다. CNN 결과가 없으면 null이고 재조회 시 최신 값을 반영한다.
- 기존 API/DB 구조/PC 탐지·저장·서버 실행 설정은 유지한다. 1단계 당시 crop_url 문자열만 제공했으며 이미지 GET은 위 2단계에서 구현했다. 상세·영상·Live·Flutter는 제외한다.
- 계약/응답 예시/Postman 순서는 [API 명세의 모바일 목록 절](../docs/API_SPEC.md#모바일-이벤트-목록-api--2026-11-21-12단계)을 모바일 목록 작업/검증 전에 읽는다. 신규 자동 검증은 `tests/test_mobile_event_api.py`에 있다.
- 1단계의 검증 범위는 임시 DB/파일의 TestClient와 로컬 임시 Uvicorn HTTP이며, 운영 DB/실제 카메라/Postman 직접 조작/다른 기기 접속은 검증하지 않았다.
- 검증 결과: 신규 8개(목록/인증 TestClient 7개 + 실제 로컬 HTTP 1개)를 포함해 백엔드 전체 **107개 통과**(51.313초). 한국 날짜 양쪽 경계·동일 시각 커서·빈 날짜·네 카드·사라짐 연결·CNN/경로 재조회·인증 실패를 확인했다. AST 7개와 git diff --check 통과. 기존 venv 실행기의 Python 경로를 사용할 수 없어 가용 python3.11.exe에 venv311/Lib/site-packages를 PYTHONPATH로 연결해 실행했고 패키지는 변경하지 않았다. 기존 Starlette/httpx 폐기 예정 경고는 유지된다.

## CNN 결과 저장 2단계 (2026-11-20)

`events.cnn_result` nullable JSON 컬럼, 인증된 `PATCH /api/v1/events/{event_id}/cnn-result`, 기존 EventResponse의 결과 조회를 추가했다. 요청은 `{"cnn_result":{"model_version":"exp3","attributes":{...}}}`이며 전체 8개 속성과 후처리 나이 범주를 검증한다. 등장 이벤트만 갱신하고 미디어·식별값·시각은 유지한다. 계약은 [API 명세 CNN 절](../docs/API_SPEC.md#6-cnn-결과--patch-apiv1eventsevent_idcnn-result), 진행/검증은 [CNN 개발 계획](../docs/CNN_APPEARANCE_DEVELOPMENT_PLAN.md)을 읽는다. 백엔드는 AI 모델을 import하지 않는다. 후속 3단계에서 PC 공용 CNNWorker→기존 EventStore의 자동 저장을 연결했고 실제 단일 웹캠 정상 흐름까지 검증했다.

다음 백엔드 시작 시 기존 events에 컬럼이 없으면 **DB 옆 자동 백업 생성 후 컬럼을 추가**한다. `migrations.py`가 단일 인스턴스 잠금 안에서 실행되며 SQLite backup API로 WAL의 커밋된 내용까지 백업한다. 백업명은 `ai_cctv.db.before-cnn-result-UTC시각-식별자.bak` 형식이다. 백업 실패 시 변경하지 않고 시작을 중단하며 ALTER 실패 시 rollback한다. 기존 행은 SQL NULL, 이미 컬럼이 있으면 추가 작업/백업은 없다. 새 DB는 create_all로 생성한다. 이 변경은 cnn_result에 한정된 마이그레이션이며 임의의 과거 스키마를 자동 복구하지 않는다. 검증은 임시 DB에서 수행했고 운영 DB는 직접 변경하지 않았다.

현재 SQLite 기반 인증 API 5개, 카메라 관리 API, 글로벌 인물 조회·등록·마지막 관측 갱신 API와 `GET /health`가 구현되어 있다. `users`, `server_runs`, `cameras`, `global_person_manage`, `events` 모델이 등록되어 서버 시작 시 없는 테이블을 생성한다. PC의 OSNet·Manager/API·카메라 큐·인증 종료·글로벌 ID 영상 표시를 연결했다. ReID 5단계 단일 인물 두 카메라 연결은 사용자 확인했으며 다른 사람/동시 인물·지속 성능은 미검증이다. 이벤트는 2단계 모델·DTO·Repository·Service·인증된 이벤트 API 4개·저장소 설정 API 2개까지 구현했으며 PC 공용 큐/카메라 연결은 3단계에서 구현했으며 인물/미디어 자동 연결도 4단계에서 구현했다. 5단계에서 합성·단일 웹캠 정상 흐름도 확인했다. 두 물리 카메라/RTSP·장시간 범위는 미검증이다. 상세 검증은 [클라이언트 README](../README.md)를 참고한다.

이벤트 코드·저장 계약을 수정하거나 다음 단계를 시작할 때 [이벤트 MVP 개발 계획](../docs/EVENT_MVP_DEVELOPMENT_PLAN.md), [DB 스키마](../docs/BACKEND_SCHEMA.md)의 events 절, [API 명세](../docs/API_SPEC.md)의 이벤트 절을 읽는다. 이벤트 Router와 Service를 앱 수명에 연결했으며 Swagger/Postman에서 기존 Access로 사용할 수 있다. 다음 제품 서버 실행 시 없는 events만 추가되며 기존 네 테이블은 초기화하지 않는다.

## 이벤트 MVP 5단계 검증 완료

재검증/완료 범위 확인 전 [통합 검증 기록](../docs/EVENT_MVP_STAGE5_VALIDATION.md)을 읽는다. 자동 121개(백엔드 88개/카메라 14개/인물 19개), 제품 GUI/작업 스레드·별도 임시 HTTP·파일까지의 합성 종합 시나리오가 통과했다. 실제 웹캠 B에서 시작/등장/사라짐/중지 각 1건과 올바른 연결·시각·최종 미디어 경로·정상 로그아웃을 확인했다. 운영 계정/DB/설정·기존 미디어는 사용/수정하지 않았다. 실제 두 카메라/RTSP·다른 사람 구분·장시간/무손실 검증으로 확대 해석하지 않는다.

## 이벤트 MVP 2단계: API·저장소 설정

- POST /api/v1/events, PATCH /api/v1/events/{event_id}/media, GET /api/v1/events, GET /api/v1/events/{event_id}와 GET/PUT /api/v1/settings/storage를 구현했다. 모두 기존 관리자 Access 인증을 사용한다. 성공 응답은 JSON DTO/no-store이며 미디어 파일은 전달하지 않는다.
- 이벤트 저장 전 이미 존재하는 동일 볼륨의 AI_CCTV 폴더를 storage_root로 설정한다. 설정은 DB 옆 ai_cctv.storage.json에 원자적으로 저장하며 첫 이벤트 커밋 이후 다른 경로 변경을 거부한다. 동일 경로는 허용한다. 별도 설정 테이블은 없다.
- 기본 파일은 client_code/backend/data/ai_cctv.storage.json이다. create_app(db_path, storage_config_path)로 임시 설정 경로를 주입할 수 있다. 단일 프로세스에서 설정 변경과 이벤트 저장을 같은 잠금으로 보호한다.
- 상대 경로 문법·기존 이벤트 폴더 구조·인물/카메라/파일명·실재 일반 파일/크기·저장소 경계·하위 링크/junction 거부를 검증한다. 폴더명 변경은 모든 현재 경로를 한 PATCH로 보낸다. Writer 종료·영상 재생 가능 여부는 PC 책임이다.
- 설정 손상은 기존 파일을 보존하며 서버 시작 실패다. 이벤트가 있는데 설정이 유실되면 다른 루트로 재설정하지 못하며 기존 설정 복원이 필요하다. 저장소가 일시적으로 없을 때도 이력 GET은 가능하다.
- 신규 임시 DB/설정/파일 TestClient 19개·실제 Uvicorn HTTP 1개를 추가했다. 기존 68개와 합쳐 **전체 88개 통과**(33.753초), 문법(AST)·diff 검사 통과. 상세 결과와 남은 제한은 [이벤트 개발 계획](../docs/EVENT_MVP_DEVELOPMENT_PLAN.md#7-2단계-구현검증-기록)에 기록한다. 운영 DB/설정·계정·기존 미디어는 사용하지 않았다.
- 3단계에서 PyQt 설정 동기화·공용 큐·카메라 시작/정상 중지 자동 저장·로그아웃 전 큐 마무리를 연결했다. 신규/회귀 133개와 임시 HTTP 검증 내역은 이벤트 개발 계획 3단계를 따른다. 4단계 인물/미디어 자동 저장도 연결했다. 등장 성공 뒤 미디어/사라짐을 순서대로 저장하고 경로 rename 경합과 종료 중 마지막 갱신을 처리한다. 해당 신규 19개·관련 회귀 147개 결과는 개발 계획 4단계를 따른다. 백엔드 제품 코드는 2단계 이후 바꾸지 않았다. UI 이력·알림/푸시·미디어 송출은 이번 전체 MVP 범위 밖이다.

## 이벤트 MVP 1단계 검증

- 신규 임시 DB 검증 15개: 네 종류 입력·NULL 조합·엄격한 타입/UUID/UTC/상대 경로 문법, FK·삭제 제한·부분 UNIQUE, 사라짐 대상/관측/시각 일치, 동시 생성·재요청/충돌, 생성/다중 경로 갱신 롤백, 안정적인 커서 조회, 기존 네 테이블 보존 및 DB 재연결 후 중복 방지를 확인한다.
- 최종 결과: 신규 이벤트 15개 + 기존 인증·카메라·글로벌 인물 53개, **전체 68개 통과**(24.098초). 기존 API의 임시 Uvicorn HTTP 회귀도 포함하며 신규 이벤트 HTTP 검증은 아니다. 문법(AST)·diff 검사도 통과했다. 기존 테스트의 테이블 목록 기대값은 events 추가에 맞게 갱신했다.
- 신규 이벤트 HTTP/Access 인증·저장소 실재/링크/파일 완료 검증·설정 유지·실제 카메라·PyQt·OSNet/종료 큐는 이번 단계에서 실행하지 않는다. 기존 API의 임시 HTTP 회귀와 신규 이벤트 HTTP 검증을 구분한다.
- 운영 DB/계정·설정·미디어는 테스트에 사용하지 않는다. 신규 테이블 생성과 기존 데이터 보존은 임시 DB에서 확인한다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/backend/tests -v
```

제한 실행 환경에서는 가용 python3.11.exe에 기존 venv311/Lib/site-packages를 PYTHONPATH로 연결해 실행했다. 패키지를 설치/업그레이드하지 않았다. 최종 실행 결과는 이벤트 개발 계획의 1단계 기록에 남긴다.

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
- 2026-11-19 ReID 1~5단계: [글로벌 인물 개발 계획](../docs/GLOBAL_PERSON_DEVELOPMENT_PLAN.md), [독립 글로벌 인물 API](../docs/API_SPEC.md#글로벌-인물-api--멀티카메라-reid-mvp), [DB 설계](../docs/BACKEND_SCHEMA.md)를 글로벌 인물·OSNet 작업 전에 읽는다. 영상 표시 구현과 단일 인물 사용자 확인까지 완료했으며 나머지 현장 검증은 계획을 따른다. 없는 테이블은 서버 시작에 생성하고 기존 DB 구조의 자동 변경·초기화는 하지 않는다. 이벤트 구현 상태는 이 문서의 최신 단계 기록을 따른다.
- API 구현·클라이언트 연동·검증 전에는 [통합 API 명세서](../docs/API_SPEC.md)를 읽는다. 인증·카메라·글로벌 인물 API 계약과 구현이 정리되어 있다. PyQt 목록·등록·수정 및 선택 카메라 실행을 연결했다. 실제 장치 검증 범위는 클라이언트 README에 구분한다.
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
- `create_all`은 없는 테이블만 생성한다. 기존 테이블 구조 변경은 위 CNN 결과 컬럼 전용 마이그레이션만 제공한다.
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

- 테스트는 `tests/test_auth.py`, `tests/test_camera.py`, `tests/test_camera_api.py`, `tests/test_global_person_api.py`에 있으며 임시 DB를 사용한다. 2026-11-18 전체 53개 통과. 실제 관리자 계정을 만들거나 운영 DB를 초기화하지 않는다.
- 테스트 의존성은 `tests/requirements.txt`에 선언했다. 현재 환경의 httpx 0.28.1을 재사용했다.
- 최초 가입 필요 여부 조회는 임시 DB에서 가입 전·후 및 서버 재시작 후 응답, 캐시 금지, DB 오류를 가입 필요로 오인하지 않는 동작을 검증한다. 기존 인증 회귀 검증과 함께 총 18개 테스트를 사용한다.
- 실제 로컬 Uvicorn HTTP로 `/health`, `/docs` HTML, `/openapi.json`, 가입→로그인→갱신→로그아웃→갱신 거부를 확인했다. 브라우저 Swagger 화면 조작이나 Postman 직접 조작, PyQt·카메라 실행을 검증한 것은 아니다.
- `pip check` 통과. 기존 Starlette는 테스트의 httpx 사용에 폐기 예정 경고를 내지만 테스트는 통과한다. 이 경고를 없애기 위한 환경 업그레이드는 하지 않았다.

## 남은 범위

- 입력 최대 길이·아이디 문자 정책은 미확정이므로 최소 4자만 적용한다. 아이디는 대소문자를 구분하며 입력을 trim·정규화하지 않는다.
- 비밀번호 변경, 이벤트 기능, Flutter용 영상 송출은 미구현이다. 글로벌 인물 저장 API까지 구현·검증했으며 실제 OSNet·매칭·PyQt 연결은 후속 범위다.
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
