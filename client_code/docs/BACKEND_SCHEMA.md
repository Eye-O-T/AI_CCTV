# CCTV 백엔드 최소 스키마 및 구현 제약

- 정리일: 2026-10-05
- 인증 정책 갱신일: 2026-10-06. 요청·응답과 세션 제약은 [통합 API 명세서](API_SPEC.md)를 참고한다.
- 상태: 설계와 단계별 구현을 함께 기록한다. 현재 이벤트 구현은 2026-11-19 1단계까지이며 API/PC 연결 완료를 뜻하지 않는다.
- 2026-10-06 구현 범위: 인증용 `users`, `server_runs`와 인증 API 5개, PyQt 인증 화면·서버 자동 시작 및 종료를 구현했다. 2026-10-07에는 Camera 모델·스키마·Repository·Service·Router와 카메라 API를 구현·검증했다. `events`는 아래 2026-11-19 진행 상태를 따르며 영상 결과 API·Flutter 연동은 미구현이다. 구현·검증 상태는 [백엔드 README](../backend/README.md)와 [클라이언트 README](../README.md)를 참고한다.
- 범위: FastAPI + SQLite 기반 관리자 인증, 카메라 등록, 이벤트 저장 및 글로벌 인물 ID 연결.
- 글로벌 인물 정책 갱신일: 2026-11-18. 아래 내용은 합의한 설계이며 모델·API·실제 DB 변경 완료를 뜻하지 않는다.
- 원칙: 기본 테이블은 `server_runs`, `users`, `cameras`, `events`, `global_person_manage` **다섯 개**로 한다. 별도 테이블·서비스·기능은 범위를 합의한 뒤 추가한다.
- 이 문서를 근거로 기존 코드를 자동으로 변경하지 않는다. 실제 구현은 사용자가 요청한 단계와 범위에서 진행한다.
- 2026-11-18 ReID 0단계 당시 목표는 이벤트·CNN을 제외한 멀티카메라 글로벌 ID 판별이었다. 당시 DB 범위는 기존 3개 + global_person_manage의 4개 테이블까지였다. 단계·모델 설정은 [GLOBAL_PERSON_DEVELOPMENT_PLAN.md](GLOBAL_PERSON_DEVELOPMENT_PLAN.md), 독립 글로벌 인물 API는 [API_SPEC.md](API_SPEC.md#글로벌-인물-api--멀티카메라-reid-mvp)를 읽는다.
- 2026-11-18 ReID 1단계: global_person_manage 모델과 독립 API를 구현했다. 당시 등록 모델은 4개였으며 events는 미구현이었다. 임시 DB·로컬 HTTP 검증은 개발 계획에 기록했고 실제 DB/백업은 수정하지 않았다.

- 2026-11-19 이벤트 1~2단계: 등록 모델은 5개이며 events 모델·DTO·Repository·Service·인증된 이벤트/저장소 설정 API를 구현했다. 임시 DB/설정/파일 및 로컬 HTTP로 검증했고 운영 DB·설정은 변경하지 않았다. 3단계 PC 큐/카메라 연결까지 구현했으며 [이벤트 개발 계획](EVENT_MVP_DEVELOPMENT_PLAN.md)을 따른다.

## 1. 시스템 역할과 저장 경계

| 구성 | 역할 |
| --- | --- |
| PC 관제 프로그램 | 카메라별 영상 수신·추적, crop·궤적·클립 생성, OSNet 및 자체 CNN 작업 |
| PC FastAPI 백엔드 | 관리자 인증, 카메라·이벤트 API, SQLite 접근, 미디어 접근 제어 |
| Flutter 앱 | 로그인, 카메라별 실시간 영상 조회, 이벤트 히스토리·상세 조회 |
| 엣지노드 | 영상 송출, 장애 대비 녹화 및 복구 API 등 |

- SQLite 접근은 백엔드로 모은다. 추적·분석 결과는 API로 등록·부분 갱신한다.
- 프레임별 상태와 현재 관측 중인 로컬·글로벌 ID 연결은 추적 프로그램 메모리에서 관리한다. 글로벌 대표 벡터는 DB에 보존하고, 공용 GlobalPersonManager가 API로 읽어 메모리 비교 후보를 구성한다.
- Refresh 토큰 상태는 **백엔드 단일 프로세스의 메모리**에서 관리한다.
- 이미지·영상은 파일 저장소에 저장하고, DB에는 저장소 기준 상대 경로만 기록한다.
- 글로벌 매칭용 OSNet과 이벤트 분석용 자체 CNN은 서로 다른 역할이다. CNN 출력 항목은 아직 미정이다.
- 기존 VLM은 자체 CNN으로 교체하고 Discord는 제거하는 방향이다. 이 문서는 그 변경을 구현하지 않는다.

## 2. 공통 자료형 및 DB 규칙

- PK는 `INTEGER PRIMARY KEY AUTOINCREMENT`를 기준으로 한다. 이벤트 ID와 서버 실행 ID의 재사용을 방지한다.
- 아래 `DATETIME`은 논리적 날짜·시간 타입이다. Python에서는 `datetime`으로 다루고 SQLite에는 UTC로 정규화한 일관된 형식의 문자열로 저장한다. 시간대·소수점 정밀도를 섞지 않는다.
- `TEXT(JSON)`은 유효한 JSON 문자열을 뜻한다. API에서 결과 구조를 검증하고 저장한다.
- 필수 값은 `NOT NULL`, 허용 상태·타입은 `CHECK` 또는 동등한 검증으로 제한한다.
- SQLite 연결마다 외래 키 검사를 활성화한다. 과거 이벤트를 보존하며 참조 대상을 무분별하게 연쇄 삭제하지 않는다.
- 보호 API는 인증·입력 검증을 수행한다. 최초 가입·로그인은 사전 토큰 없이 각각 가입 조건·계정 정보를 검증하고, 갱신·로그아웃은 Refresh 토큰으로 처리한다. `/health`는 인증 없는 생존 확인이다. 정수 ID를 알고 있다는 사실은 접근 권한이 아니다.
- DB 초기화·과거 백업 복원 시에는 JWT 서명 키도 교체해, 되돌아간 실행 번호 때문에 과거 토큰이 다시 유효해지지 않게 한다.

## 3. 서버 실행 기록: `server_runs`

| 필드 | 타입 | NULL | 설명 |
| --- | --- | --- | --- |
| `server_run_id` | INTEGER | 불가 | PK, AUTOINCREMENT |
| `started_at` | DATETIME | 불가 | 실행 시작 시각 |
| `ended_at` | DATETIME | 허용 | 정상 종료 시각, 초기 NULL |
| `status` | TEXT | 불가 | `running`, `stopped`, `interrupted` |

### 정책

- 서버 시작마다 새 행을 생성한다. 현재 실행 ID는 실행 중 고정한다.
- 정상 종료 시 `stopped`와 종료 시각을 기록한다.
- 다음 실행에서 이전 행이 `running`이면 중복 실행이 아닌 것을 확인한 후 `interrupted`로 변경한다.
- 강제 종료 시각은 알 수 없으므로 복구 시각을 종료 시각인 것처럼 기록하지 않는다.
- Access JWT에 현재 실행 ID를 포함하고, 현재 값과 다르면 만료 전이라도 거부한다.
- Refresh 토큰의 메모리 기록도 현재 실행에 귀속시킨다.

## 4. 관리자 계정: `users`

| 필드 | 타입 | NULL | 설명 |
| --- | --- | --- | --- |
| `user_id` | INTEGER | 불가 | PK, AUTOINCREMENT |
| `username` | TEXT | 불가 | UNIQUE, 로그인 이름 |
| `password_hash` | TEXT | 불가 | 비밀번호 해시. 원문 저장 금지 |
| `created_at` | DATETIME | 불가 | 계정 생성 시각 |
| `updated_at` | DATETIME | 불가 | 계정 수정 시각 |

### 인증 정책과 제한

- 관리자 계정만 사용한다. `role`, `viewer`, `is_active`는 초기 범위에서 제외한다.
- 최초 관리자 생성은 관리자 계정이 없을 때만 회원가입 API로 허용한다. 아이디·비밀번호는 각각 4자 이상이며 비밀번호 확인이 일치해야 한다. 동시 가입에도 계정 하나만 생성하도록 보장한다. 가입 후 추가 가입은 차단한다.
- PC 관제 프로그램과 Flutter 앱은 같은 계정 체계를 사용하되 각각 로그인한다.
- Access JWT와 Refresh 토큰을 사용한다. Access 토큰 원문을 DB에 저장하지 않는다.
- `auth_sessions`, `refresh_tokens` 테이블은 만들지 않는다.
- 메모리에 로그인별 세션 ID, Refresh 토큰 해시, 사용자, 실행 ID, 최종 만료 등 필요한 상태를 보관한다. Access JWT에 세션 ID를 포함하고 보호 요청마다 세션 유효성을 확인한다. PC와 앱의 토큰이 서로 덮어써지지 않게 한다.
- Refresh 갱신의 검증·기존 토큰 제거·새 토큰 등록은 원자적으로 처리한다. 단순 교체 방식은 과거 토큰의 상세 재사용 이력까지 제공하지 않는다.
- JWT 서명·허용 알고리즘·발급자·대상·만료·토큰 용도 및 현재 실행 ID를 검증한다. Refresh 토큰을 일반 조회용 Access 토큰으로 인정하지 않는다.
- 서버 종료·재시작 후 이전 Access·Refresh 토큰은 모두 거부한다.
- 단일 프로세스 메모리 정책이므로 여러 API worker 또는 여러 백엔드 인스턴스를 그대로 실행하면 안 된다. 필요해지면 공유 상태 설계를 다시 논의한다.
- Access 토큰은 발급부터 최대 15분, Refresh 토큰과 로그인 세션은 최초 로그인부터 최대 8시간이다. 갱신 시 Refresh 값을 교체해도 최종 만료 시각은 연장하지 않는다. Access 만료도 세션 최종 만료를 넘지 않는다. 서버는 Refresh의 SHA-256 해시만 보관한다. PyQt는 토큰을 메모리에만 보관하며 Flutter 보관 방식은 앱 연결 전에 결정한다.
- 개별 로그아웃은 해당 세션의 Access·Refresh 토큰을 모두 즉시 무효화한다. 다른 로그인 세션은 유지한다. PyQt는 영상 수신·녹화·분석·부가 작업 종료 후 서버 로그아웃을 호출하고 두 토큰을 삭제하여 로그인 화면으로 돌아간다. 로그아웃은 서버를 유지하며 프로그램 종료 시에는 자신이 실행한 서버도 종료한다.

## 5. 카메라 등록 정보: `cameras`

| 필드 | 타입 | NULL | 설명 |
| --- | --- | --- | --- |
| `camera_id` | INTEGER | 불가 | PK, AUTOINCREMENT |
| `name` | TEXT | 불가 | 관제 화면·앱 표시 이름 |
| `camera_type` | TEXT | 불가 | `webcam`, `edge_node` |
| `webcam_index` | INTEGER | 조건부 | 웹캠 장치 번호 |
| `rtsp_url` | TEXT | 조건부 | 엣지노드 영상 수신 주소 |
| `edge_api_base_url` | TEXT | 허용 | 엣지노드 복구·장치 API 기본 주소 |
| `is_enabled` | INTEGER | 불가 | 0 또는 1, 기본 1 |
| `created_at` | DATETIME | 불가 | 등록 시각 |
| `updated_at` | DATETIME | 불가 | 수정 시각 |

### 제약 및 동작

- 카메라 관리 API의 상세 계약은 [통합 API 명세서의 카메라 API 절](API_SPEC.md#카메라-api)을 따른다. 2026-10-07 Camera 모델·스키마·Repository·Service·Router와 API 검증을 완료했다. PyQt 관리 화면·단일 카메라 실행을 연결했다. 실제 장치 검증 범위는 클라이언트 README를 참고한다.
- `name`은 앞뒤 공백 제거 후 1~100자이며 중복을 허용한다. SQLite에서도 길이 CHECK를 적용한다.
- `camera_type`은 등록 후 변경하지 않는다. `webcam_index`는 0 이상이며 UNIQUE, `rtsp_url`도 UNIQUE로 제한한다. NULL은 여러 행에서 허용하며 비활성 등록도 중복 제약에 포함한다.
- 주소는 앞뒤 공백만 제거해 저장한다. RTSP 주소는 rtsp/rtsps, 장치 API 주소는 http/https를 허용한다. URL userinfo는 거부하며 자격증명을 URL에 저장하지 않는다. 주소 검증·정확한 문자열 중복 기준은 API 명세를 따른다.
- `webcam`: `webcam_index` 필수, RTSP·엣지노드 API 주소는 NULL.
- `edge_node`: `rtsp_url` 필수, `webcam_index`는 NULL. 복구·장치 API 사용 시 `edge_api_base_url`도 필수.
- RTSP와 API 주소를 구분한다. 호스트·포트가 반드시 같다고 가정하지 않는다.
- 등록과 연결 성공을 구분한다. 연결 실패가 나도 등록 정보는 유지하고 재시도할 수 있다.
- `is_enabled`는 사용 설정이며 실제 연결 상태가 아니다. 연결 상태는 실행 중 별도로 관리한다.
- 웹캠 장치 번호는 USB 연결 상황에 따라 달라질 수 있으므로 재선택을 지원해야 한다.
- 각 카메라는 독립적인 수신·추적기·재연결·녹화 상태를 갖는다. 한 카메라 장애가 다른 카메라를 종료시키면 안 된다.
- 초기에는 카메라 삭제 대신 비활성화를 사용한다.
- 엣지노드 자격증명, 장치 등록 인증 및 앱용 실시간 스트리밍 방식은 아직 미정이다. 이 스키마만으로 장치 보안·실시간 송출 구현이 완성되는 것은 아니다.
- 앱에 원본 RTSP 주소나 자격증명을 무조건 반환하지 않는다. 앱의 시청 권한과 송출 방식에 맞춘 API 계약을 별도로 정한다.

## 6. 이벤트: `events` — 2026-11-19 MVP

**2단계 모델·DTO·Repository·Service·이벤트 HTTP API·저장소 설정 구현 완료. PC 큐/카메라 연결과 4단계 인물/미디어 자동 전송까지 구현했다. 5단계 장치 종합 검증은 남아 있다.** [이벤트 개발 계획](EVENT_MVP_DEVELOPMENT_PLAN.md)을 이벤트 작업 전에 읽는다. 아래 정책은 과거 person_detected/매칭 대기 설계를 대체한다. OSNet/ReID는 이벤트 응답에 의존하지 않는다.

| 컬럼 | NULL | 의미 |
| --- | --- | --- |
| event_id | 불가 | INTEGER PK AUTOINCREMENT |
| event_type | 불가 | person_appeared / person_disappeared / camera_started / camera_stopped |
| server_run_id | 불가 | FK → server_runs, 백엔드가 현재 실행 ID 부여 |
| camera_id | 불가 | FK → cameras |
| camera_execution_id | 불가 | PC 카메라 실행 UUID v4, TEXT 표준 하이픈 형식 |
| tracking_generation | 인물 필수 | 0 이상 정수 추적 세대 |
| local_person_id | 인물 필수 | 양의 정수 로컬 ID |
| global_person_id | 인물 필수 | FK → global_person_manage |
| related_event_id | 사라짐 필수 | FK → events의 같은 관측 등장 |
| occurred_at | 불가 | UTC aware datetime, 기존 UTCDateTime의 고정 소수 6자리 TEXT |
| crop_path | 허용 | 저장소 AI_CCTV 기준 상대 경로 |
| clip_path | 허용 | 위와 같음 |
| trajectory_path | 허용 | 위와 같음 |

### 발생·NULL 조합

- 등장: 글로벌 ID 확정 후 생성, occurred_at은 첫 탐지 시각. 미디어는 준비된 것만 저장하고 나머지는 NULL.
- 사라짐: 정상 수신 중 약 3초 미탐지, occurred_at은 마지막 탐지 시각. related_event_id 필수, 미디어 세 개는 모두 NULL.
- 시작: 실행 UUID의 첫 프레임 수신 성공 1회. 재연결은 새 시작이 아니다.
- 중지: 첫 프레임을 받은 실행에서 사용자 STOP/정상 종료의 정리 성공 시점. 시작 실패·강제 종료·오류/정리 실패는 정상 중지로 기록하지 않는다.
- 카메라 이벤트는 추적 세대·로컬/글로벌 ID·related_event_id·미디어 모두 NULL. 등장의 related_event_id도 NULL.
- STOP·영상 끊김·추적기 초기화는 미디어만 마무리하고 사라짐을 만들지 않는다. finished_at 존재만으로 자연 유실을 판단하지 않는다. 미확정 글로벌 ID 관측은 인물 이벤트에서 제외한다.

### 무결성·중복

- DB CHECK: 네 종류 제한, 종류별 필수/금지 NULL, 추적 세대/로컬 ID 범위, 사라짐 자기 참조 금지. FK는 ON DELETE RESTRICT이며 연쇄 삭제하지 않는다.
- DTO: JSON 정수의 bool/문자열/실수 변환 거부, UUID v4 정규화, 시간대 필수·UTC 정규화, 알 수 없는 필드 거부. UUID hex 입력은 표준 문자열로 저장한다.
- Service: 현재 running 서버 실행 존재, 참조 존재, 사라짐의 대상이 등장인지 및 서버/카메라/실행 UUID/세대/로컬/글로벌 ID 전체 일치, 등장 ≤ 사라짐 시각을 검증한다. 관련 행 일치는 단순 self-FK만으로 보장되지 않는다.
- 인물 부분 UNIQUE: (server_run_id, camera_id, camera_execution_id, tracking_generation, local_person_id, event_type), WHERE event_type IN ('person_appeared', 'person_disappeared').
- 카메라 부분 UNIQUE: (server_run_id, camera_id, camera_execution_id, event_type), WHERE event_type IN ('camera_started', 'camera_stopped'). NULL 인물 컬럼에 의존해 카메라 중복을 막지 않는다.
- 현재 StablePersonIdMapper는 종료한 로컬 ID를 재사용하지 않는다. PC는 crop 작업 UUID도 검증해 늦은 결과를 차단한다. 향후 이 전제가 바뀌면 관측 키를 재검토한다.
- Service는 BEGIN IMMEDIATE로 중복 조회·검증·삽입을 보호한다. 같은 키·같은 불변 정보 재전송은 기존 행 반환, 불변 내용 충돌은 409. 생성 재요청의 미디어는 무시하고 현재 경로를 반환한다. 미디어 변경은 PATCH를 사용한다.
- Repository는 commit하지 않는다. 생성/다중 미디어 갱신 실패는 Service에서 전체 rollback한다.

### 미디어·조회·저장소

- 미디어는 등장에만 저장한다. 파일 작성 완료 → 사용자 최종 위치 이동 성공 → 상대 경로 갱신 순서이며 temp 경로를 등록하지 않는다. 파일별 완료·이동 순서는 다를 수 있다.
- 상대 경로는 / 구분자이며 절대/드라이브/UNC/상위 이동/빈 항목/역슬래시/제어 문자 등을 거부한다. 2단계 Service는 기존 이벤트 폴더 구조·인물/카메라·파일명 일치, 실제 일반 파일 존재/비어 있지 않음, 저장소 경계와 링크/junction/reparse point 거부를 검사한다. 미디어들은 같은 관측 폴더여야 한다. Writer 종료·이동 완료와 실제 파일 내용은 PC 책임이며 백엔드 파일 검증으로 이를 증명하지 않는다.
- 폴더명 변경 후 모든 현재 경로를 한 PATCH/트랜잭션으로 반영한다. PATCH 생략은 유지, 명시 null은 경로 제거. PC는 관측별 한 요청과 최신 revision 합치기로 순서를 유지한다. 자동 갱신은 준비된 경로만 보내므로 옛 요청이 새 파일을 NULL로 지우지 않는다. folder rename 경합으로 생성이 거부되면 같은 불변 키/시각으로 미디어 없는 생성을 한 번 재확인한 뒤 현재 경로를 PATCH한다. NULL은 이용 불가이며 상세 실패 상태는 저장하지 않는다.
- 목록은 occurred_at DESC, event_id DESC, 동일 순서 인덱스와 배타적 두 값 커서. 기본 50/최대 100. 복잡한 검색/통계/구독 없음.
- 저장소 절대 경로는 백엔드 설정 파일로 유지한다. 이벤트 저장 전 설정 필수, 첫 이벤트 커밋 뒤 다른 경로 변경 제한(동일 경로 허용). 2단계는 DB 옆 ai_cctv.storage.json을 원자적으로 교체하고 설정/이벤트 쓰기를 잠금으로 조정한다. 기존 AI_CCTV 폴더·동일 볼륨·프로젝트 temp/output 제외를 검증한다. 설정 손상은 시작 실패, 이벤트가 있는 DB의 설정 유실은 다른 경로 설정을 거부한다. 3단계 GUI는 백엔드 설정을 복원/확인하고 확인된 경로만 Worker에 전달하며 촬영 중 변경을 차단한다. 설정 테이블은 추가하지 않는다.
- 글로벌 매칭 상태·message·CNN·별도 미디어 테이블·파일 응답/Range는 이번 MVP 범위 밖이다.

## 7. 글로벌 인물: `global_person_manage` 및 매칭 정책

| 필드 | 타입 | NULL | 설명 |
| --- | --- | --- | --- |
| `global_person_id` | INTEGER | 불가 | PK, AUTOINCREMENT. 이벤트 ID와 독립적으로 DB가 발급 |
| `feature_vector` | BLOB | 불가 | 최초 crop 5장으로 생성한 L2 정규화 대표 벡터 |
| `created_at` | DATETIME | 불가 | 글로벌 인물 DB 등록 시각 |
| `updated_at` | DATETIME | 불가 | 대표 벡터의 마지막 갱신 시각. 생성 시 created_at과 동일 |
| `last_seen_at` | DATETIME | 불가 | 연결된 관측의 마지막 발생 시각 |

### 저장 규칙

- 대표 벡터는 고정 차원 D의 little-endian float32 연속 바이트로 저장한다. 길이는 4×D 바이트이며 NaN·무한대·영벡터를 허용하지 않는다. JSON API에서는 숫자 배열을 사용하고 백엔드가 BLOB으로 변환한다.
- 1단계 DB CHECK는 BLOB 타입과 2048바이트 길이를 보장한다. 수치·norm은 DTO/공통 벡터 코드에서 저장 전 및 조회 복원 시 검증한다. 직접 SQL로 쓴 잘못된 수치를 정상 후보로 반환하지 않는다.
- ReID 0단계에서 OSNet x1.0 / MSMT17 combineall, 입력 H×W=256×128, 출력 D=512로 정했다. BLOB은 2048바이트다. RGB·전처리·가중치 출처·실행 장치는 개발 계획을 따른다. L2 단위 길이 검증 허용 오차는 0.001이다.
- 초기에는 최초 대표 벡터를 유지한다. 기존 인물 재매칭은 feature_vector와 updated_at을 변경하지 않고 last_seen_at=max(기존 값, API observed_at)만 반영한다. observed_at은 이번 대표 crop의 실제 관측 시각이며 프레임별 갱신은 하지 않는다. 후속 이벤트 연결에서는 occurred_at과 대응한다.
- 대표 벡터 갱신·글로벌 인물 삭제 API는 초기 범위에서 제외한다. 모델·가중치·전처리 변경 시 기존 벡터를 섞어 비교하지 않고 초기화 또는 재추출 방식을 먼저 정한다.
- events의 FK에는 ON DELETE RESTRICT(또는 동등한 NO ACTION)를 적용한다. 이벤트와 글로벌 인물을 연쇄 삭제하지 않는다.

### 현재 ReID MVP 처리 흐름

```text
카메라별 CropWorker: 전신 crop 5장 저장
    ↓
카메라별 OSNetWorker: 각 벡터 L2 정규화 → 5개 평균 → 다시 L2 정규화
    ↓
공용 GlobalPersonManager의 제한된 큐
    ├─ 비교 후보의 최고 코사인 유사도 ≥ 임계값 → 기존 글로벌 ID 연결
    └─ 후보 없음 또는 최고 유사도 < 임계값 → 신규 글로벌 인물 생성·연결
    ↓
백엔드 API 저장 완료 → 메모리 후보·현재 관측 연결 반영 → 다음 큐 작업
```

- 신규 인물의 글로벌 ID는 백엔드가 INSERT 후 발급받는다. event_id를 글로벌 ID로 사용하지 않는다.
- 시작·재시작 시 로그인 후 글로벌 인물 조회 API로 전체 대표 벡터를 읽어 후보를 복원한 뒤 매칭을 시작한다. 초기에는 시간 만료 없이 저장된 모든 인물을 비교 대상으로 한다.
- OSNetWorker는 카메라별 비동기 스레드로 동작한다. 공용 큐에는 task_id, camera_id, execution_id, tracking_generation, local_person_id, 대표 벡터, 관측 시각을 전달한다. event_id는 필요 없다. 상세 정책은 개발 계획을 따른다.
- 동일 관측의 중복 제출을 막고 큐 크기를 제한한다. 큐가 찬 경우 저장 완료된 작업은 상한이 있는 보류 영역에서 재제출하며 영상 스레드를 대기시키지 않는다. 상한에 도달하면 새 작업 접수를 멈추고 안내한다. 종료 시 남은 작업을 메모리에서 취소하며 이번 단계에는 이벤트 상태를 저장하지 않는다.
- 후보 비교·연결/신규 결정·DB 반영·메모리 후보 반영은 중앙 담당 하나가 순차 처리한다. ID 발급 시점만 잠그는 것으로 끝내지 않는다.
- 이번 단계에서 신규 인물 생성은 독립 등록 API로 저장한다. API 성공 후에만 메모리 후보를 반영하고 다음 판단을 시작한다. 응답 유실 시 같은 request_id와 본문으로 재전송하여 확인한다. 단일 서버 실행 동안 메모리 재시도 기록을 유지하고 재시작 뒤 이전 미확정 작업은 재전송하지 않는다. 이벤트는 글로벌 인물 저장 성공 이후 별도 EventService 트랜잭션으로 추가하며 기존 매칭 처리에 event_id를 요구하지 않는다.
- 영상 수신·특징 추출·CNN 전체를 직렬화하는 것은 아니다. 글로벌 연결 결정을 중앙에서 조정한다.
- 다른 카메라의 동시 관측은 동일 인물일 수 있다. 같은 카메라에서 현재 관측 중인 다른 로컬 인물에 연결된 글로벌 ID는 후보에서 제외한다. 현재 관측 연결은 메모리에서 관리하고 추적 종료·카메라 중지 시 해제하며 DB의 인물은 보존한다.
- 동점이면 global_person_id가 작은 후보를 선택한다. 임계값은 실제 같은 사람·다른 사람 촬영으로 정한다. 초기에는 1·2위 점수 차이나 모호함으로 보류하지 않는다. 오매칭은 실측 후 개선한다.
- 고정된 겹침 카메라에서 호모그래피는 추후 보완 수단이다. 초기 필수 구현으로 넣지 않는다.

## 8. 표시와 API 경계

이번 이벤트 MVP는 DB 저장·경로 갱신·JSON API 조회까지다. PyQt 이벤트 이력/상세 UI, Flutter, 알림/푸시, 정적 미디어 마운트·이미지/영상 응답·Range는 별도 후속 범위다. 기존 영상의 글로벌 ID 표시는 유지한다. 저장 설정·실패 안내 UI는 연결 단계에서 필요한 범위로 조정할 수 있다.

## 9. 시작·종료·파일 처리

- 백엔드 단일 인스턴스 확인 → 기존 테이블 보존/없는 테이블 생성 → 새 서버 실행 기록 → 인증/API 준비 순서다. 과거 이벤트의 미완료 매칭 상태 정리 정책은 폐기한다. events에는 해당 상태 컬럼이 없다.
- 로그인 후 저장소 기준을 확인하고 새 작업을 시작한다. 과거 미확정 이벤트 요청을 새 로그인/서버 실행 뒤 자동 재전송하지 않는다.
- 정상 종료는 영상·미디어 정리 → 이벤트 큐의 제한 시간 내 마지막 저장 → 인증 폐기 → 백엔드 종료 순서로 연결한다. 3단계에서 카메라 이벤트 큐의 제한 시간 마무리까지 연결했다. 4단계에서 인물 마지막 미디어 알림과 생성 확인 후 종속 요청도 제한 시간 마무리에 연결했다.
- 이벤트 큐는 ReID 큐와 별개다. 영상/GUI는 API를 기다리지 않는다. 종료 중 UI 신호 차단이 마지막 미디어 갱신까지 버리지 않도록 연결 단계에서 분리한다.
- 파일과 DB는 하나의 원자적 트랜잭션이 아니다. 실패/미확정 temp와 기존 미디어는 보존하며 자동 복구·이동 재시도·영속 outbox를 추가하지 않는다. 저장 실패를 성공으로 표시하지 않는다.
- 첫 5장 OSNet 원본은 output에 보존한다. 이벤트 대표 crop은 별도 복제한다. 최종 폴더명 변경/같은 볼륨 rename/충돌 순번 등 기존 저장 정책은 바꾸지 않는다.

## 10. 후속 검증

단계별 범위와 결과는 [이벤트 개발 계획](EVENT_MVP_DEVELOPMENT_PLAN.md)을 따른다. 1단계 임시 DB 검증과 2단계 인증 HTTP, 3~4단계 카메라/인물 연결, 5단계 합성/실장치 검증을 구분한다. ReID 정확도·GPU 지속 성능·두 카메라/RTSP·강제 종료 복구는 이벤트 DB 테스트 통과로 완료 처리하지 않는다.
