# 인상착의 CNN 개발 계획

- 기준일: 2026-11-20. 사용자 승인 범위: **1~3단계 구현 완료**. 3단계는 자동 검증·실제 단일 웹캠 정상 흐름 범위이며 두 물리 카메라/장시간 안정성 검증 완료를 뜻하지 않는다.
- CNN 작업 전 이 문서와 [추론 사용법](../appearance/README.md)을 읽는다. 2단계 시작 전 BACKEND_SCHEMA.md, backend/README.md, API_SPEC.md의 이벤트 계약도 확인한다.
- 기존 추적/crop 5장/OSNet/글로벌 ID/녹화/미디어/이벤트 종류·발생 기준을 유지한다. 기존 VLM 제거와 화면 개편은 이 범위에 포함하지 않는다.

## 합의한 흐름

```text
글로벌 ID 확정 → 등장 이벤트 저장(cnn_result=NULL)
→ event_id 확보 + 대표 crop 준비 → 공용 CNN 큐 → CNNWorker 1개
→ 추론 및 나이 후처리 → EventCoordinator → 기존 EventStore
→ HTTP PATCH → 같은 등장 이벤트의 cnn_result 갱신
```

- CNN 완료 시 새 이벤트를 만들지 않는다. 영상/ReID는 CNN을 기다리지 않는다.
- 등장 event_id당 한 번 분석한다. 같은 글로벌 ID의 다른 등장 이벤트는 별도 분석한다.
- 현재 대표 이미지는 품질 조건 통과 5장 중 첫 crop이다. 3단계 큐 입력은 대표 이미지와 동일한 output 원본 경로를 유지해 이벤트 폴더 rename 영향을 피한다.
- 공용 CNNWorker가 모델 하나를 재사용한다. 백엔드는 모델을 import/실행하지 않고 JSON 검증과 저장만 담당한다.

## 단계

| 단계 | 범위 | 상태 |
| --- | --- | --- |
| 1 | 원본 가중치 배치, 단독 추론, 속성 JSON 및 나이 후처리, 실제 crop 검증 | 완료 |
| 2 | events.cnn_result nullable JSON 컬럼, 기존 DB 보존 마이그레이션, PATCH/조회 DTO, 임시 DB 검증 | 완료: 임시 DB/HTTP 범위 |
| 3 | 공용 CNN 큐/Worker, 제출 조건·중복 방지, 기존 EventStore 저장, 종료 연결 및 한 카메라 검증 | 완료: 자동·실제 단일 웹캠 정상 흐름 |

## 1단계 구현·검증 기록

- appearance/analyzer.py: 고정 SHA-256 bundle 읽기, ResNet34+8개 head strict 로딩, RGB/Resize/정규화, 단일 이미지 추론과 출력 후처리.
- appearance/__main__.py: DB/GUI 없이 단독 실행하는 CLI. 가중치는 원본 이름·내용으로 복사했고 기존 *.pt 무시 규칙을 유지했다.
- 기존 모델의 나이 예측을 바꾸지 않고 원래 클래스를 선택한 후 소년/소녀/청년/장년으로 변환한다. JSON은 model_version과 attributes로 구성한다.
- 자동 7개 통과. 기존 저장 crop 1장의 CPU CLI/CUDA 실제 추론 성공. 원본/복사본 해시 일치, diff/문법/import/다른 cwd 기본 경로 확인. 상세 실행법과 제한은 appearance/README.md를 따른다.
- DB·백엔드·기존 실행 파이프라인·기존 미디어는 수정하지 않았다. 카메라를 실행하거나 새 이벤트를 생성하지 않았다. 정답 정확도와 실시간 동시 처리 성능을 검증한 것은 아니다.

## 2단계 구현·검증 기록

- Event 모델에 JSON(none_as_null=True) cnn_result를 추가했다. DB CHECK는 등장 이벤트의 유효한 JSON 객체 또는 SQL NULL만 허용한다. 신규 등장/사라짐/카메라 이벤트의 초기값은 모두 SQL NULL이다.
- schemas/cnn_result.py에 AI 의존성 없는 DTO를 추가했다. exp3·8개 속성·유효 라벨·배열 수/중복·소년/소녀와 성별 일치를 검증한다. 요청에서 cnn_result를 생략하거나 null로 보내는 것은 거부한다.
- `PATCH /api/v1/events/{event_id}/cnn-result`: 기존 관리자 Access 인증, Router→Service→Repository, BEGIN IMMEDIATE 트랜잭션/롤백. 등장의 결과 객체 전체를 교체하고 다른 종류는 409다. 과거 실행의 등장도 갱신 가능하다. 같은 결과 재전송은 허용하고 다른 유효 결과는 마지막 커밋이 반영된다.
- 생성 요청/미디어 PATCH에서 결과를 받지 않는다. 기존 EventResponse(생성/미디어/목록/상세)에 cnn_result를 포함한다. 등장 POST 재전송은 분석 결과를 보존한다. CNN PATCH와 미디어 PATCH의 동시 실행도 각 필드를 보존한다.
- migrations.py를 백엔드 시작의 단일 인스턴스 잠금 안에 연결했다. 기존 events에 컬럼이 없을 때 SQLite backup API로 DB 옆 `.before-cnn-result-UTC시각-식별자.bak`를 생성한 뒤 ALTER TABLE ADD COLUMN을 수행한다. 백업 실패 시 ALTER하지 않으며 ALTER 실패는 rollback한다. 기존 행/다른 테이블/키/제약을 보존하고, 재실행은 건너뛴다. 새 DB는 create_all로 생성한다. 운영 DB는 이번 작업에서 실행/변경하지 않았다. 다음 제품 백엔드 실행 때 기존 DB가 자동 갱신된다.
- 신규 테스트 11개 포함 **백엔드 전체 99개 통과**(38.937초). 정상/재전송/조회/SQL NULL/JSON 저장, 입력 거부, 인증 없음·Refresh 오용·만료·로그아웃·재시작, 비등장/부재/ID 오류, flush 후 롤백, 미디어 동시 갱신, 기존 DB 백업·행/제약 보존·재실행·백업 실패·전체 스키마 시작 자동 마이그레이션을 확인했다.
- 기존 실제 Uvicorn HTTP 테스트에 CNN PATCH/목록·상세/생성 재요청 보존 검증을 추가하고 해당 테스트를 다시 실행해 통과했다. 서버/DB/설정/미디어는 모두 임시 자료다.
- 클라이언트 관련 검증: 이력 8개 통과, 인물 이벤트 19개·자동 반영 6개 중 24개 첫 실행 통과. 인물 정상 흐름 1개에서 종료 후 crop 경로에 `~`가 없는 실패가 한 번 발생했으며 해당 테스트 단독 재실행은 통과했다. 원인은 확정하지 않았고 기존 미디어/종료 제품 코드는 수정하지 않았다. 이를 전부 안정적 통과한 것으로 해석하지 않는다. 최초 직접 모듈 실행에서 인물/자동 반영 두 모듈 import 경로가 누락됐고 client_code/tests를 PYTHONPATH에 추가한 후 실행했다.
- 문법(AST)/diff 검사 통과. 운영 DB·계정·설정·기존 미디어와 가중치는 수정하지 않았다. 실카메라·GUI 직접 조작·실제 CNN→HTTP 자동 연결·동시 AI 부하/장시간 범위는 미검증이다. CNNWorker/큐/자동 제출/종료 순서는 3단계로 남긴다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`. 기존 환경이 정상일 때:

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/backend/tests -v
```

이번 제한 환경에서는 기존 venv 실행기가 원래 Python을 찾지 못해 가용 Python에 기존 패키지를 연결했다. 설치/업그레이드는 하지 않았다.

```powershell
$env:PYTHONPATH = 'C:\GitHub_Clone\AI_CCTV\venv311\Lib\site-packages'
& 'C:\Users\jdh45\.local\bin\python3.11.exe' -B -m unittest discover -s client_code/backend/tests -v
```

## 3단계 구현·검증 기록

### 구현

- workers/cnn_worker.py: 로그인 세션의 공용 작업 큐(32개), 결과 큐(64개), 단일 Worker/모델. 모델 import/해시 검사/로딩과 이미지 읽기·추론은 모두 작업 스레드에서 수행한다. 첫 유효 등장 작업에서 지연 시작하며 이후 모델을 재사용한다. 모델 초기화 실패는 해당 세션에서 재로딩하지 않고 안내한다. 개별 이미지 분석 실패는 다음 작업을 막지 않는다.
- ClipManager/PersonMediaUpdate: 대표 crop 복제 성공 후 `cnn_source_path`에 원래 output crop의 절대 경로를 보존한다. 기존 crop 5장·대표 이미지 선정·이벤트 폴더 이동 정책은 유지한다. 내부 정보만 추가했으며 source 경로를 DB/응답으로 보내지 않는다.
- EventCoordinator: 등장 성공 응답의 event_id/server_run_id 확보와 source 준비를 모두 확인하고 event_id당 한 번 제출한다. 기존 관측 키/task UUID/revision 검증을 유지한다. Camera STOP/관측 종료 후에도 이미 확인된 이벤트의 분석·저장은 계속하며, CNN 대기 정보를 관측 상태와 분리해 보존한다. 대기는 64건으로 제한한다.
- CNN 결과는 기존 EventStore에 인증 PATCH로 전달한다. 응답의 event_id/서버 실행/관측 식별값/발생 시각/결과 일치를 확인한다. CNN PATCH는 event_saved 신호를 내지 않아 새 타임라인 카드를 만들지 않는다. 기존 토큰 갱신·HTTP 재시도 규칙을 재사용한다.
- 실패/큐 초과는 notice로 안내하고 cnn_result=NULL 및 기존 등장 이벤트를 유지한다. 새 분석 테이블/상태 컬럼/이벤트 종류/외부 서비스는 추가하지 않았다.
- 정상 종료: 영상/ReID/미디어 정리 → 마지막 등장 응답 및 분석 제출 → CNN 마무리 → 결과 PATCH를 포함한 EventStore 마무리 → 로그아웃. 기존 8초 예산을 공유한다. 초과하면 실패 안내 후 대기 작업·늦은 결과를 버린다. 실행 중 native 추론/모델 로딩은 강제 중단하지 않으며 Worker가 반환·종료될 때까지 화면 전환을 기다린다. 취소한 결과를 다음 로그인으로 넘기지 않는다. 진행 중 HTTP가 서버에 이미 커밋됐을 가능성까지 취소하는 것은 아니다.

### 자동 검증

- 신규 test_cnn_pipeline.py 11개: 모델 1회 로딩·작업 스레드 분리·순차 처리·큐 초과, 초기화/이미지 실패, 취소·실행 중 늦은 결과 차단, 등장 저장/이미지 준비 의존 및 중복 방지, 관측 종료 후 결과 저장·새 카드 없음, HTTP 실패/잘못된 ACK, 두 카메라·동일 글로벌 ID의 별도 이벤트 작업, 종료 의존 순서/시간 초과를 확인한다. 고정 가중치의 실제 CPU 추론→임시 HTTP→DB JSON→폴더 rename→정상 종료 시나리오도 포함한다.
- 최종 관련 **80개 통과(34.515초)**: 위 신규 11개, 인물 이벤트/카메라 이벤트·이력·자동 반영·인물 미디어·글로벌 ID별 미디어 이동 회귀. 로그: `temp/cnn_stage3_regression.log`.
- 첫 관련 실행에서는 기존 인물 정상 흐름 테스트가 다시 한 번 `crop_path`에 종료 폴더의 `~`가 없다고 실패했다. 2단계에서도 관찰한 간헐 현상이며 단독 진단 재실행은 통과했다. 기존 미디어 회귀가 모델 파일/GPU에 의존하지 않도록 해당 테스트에 가짜 CNN을 주입하고 정리를 명시한 뒤 최종 80개가 통과했다. 미디어 제품 로직의 잠재 원인을 수정/해결했다고 주장하지 않는다. 실제 CNN은 별도 통합 테스트와 웹캠 진단에서 검증했다.
- 백엔드 제품 코드는 2단계 이후 변경하지 않아 당시 99개 결과를 재사용했다. 이번 단계에서 모델 추론을 API 요청 처리에 추가하지 않았다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`. 제한 환경에서 실행한 명령:

```powershell
$env:PYTHONPATH = 'C:\GitHub_Clone\AI_CCTV\client_code\tests;C:\GitHub_Clone\AI_CCTV\client_code;C:\GitHub_Clone\AI_CCTV\venv311\Lib\site-packages'
$env:QT_QPA_PLATFORM = 'offscreen'
& 'C:\Users\jdh45\.local\bin\python3.11.exe' -B -m unittest test_cnn_pipeline test_event_person test_event_camera test_event_live test_event_history test_person_media test_person_media_publish -v
```

### 실제 단일 웹캠 검증

- 사용자가 전신 촬영과 화면 밖 이동에 참여했다. `manual_event_mvp_probe.py --mode webcam --index 0 --label cnn_stage3_webcam_a --timeout 90 --auto-finish-after-disappearance --require-cnn`으로 제품 GUI/VideoWorker/YOLO/OSNet/GlobalPersonManager/CNN/별도 백엔드를 실행했다. 자동 START 후 자연 사라짐과 CNN 저장이 확인되면 STOP/로그아웃한다. 기존 진단에 --require-cnn과 CNN Worker 정리를 추가했다.
- **passed=true**. 장치 0, 1280×720, 표시 프레임 583개. global_person_id=1, 등장 event_id=2. 시작/등장/사라짐/정상 중지 각각 1건. 해당 등장의 cnn_result가 비어 있지 않고 API JSON과 SQLite 저장 JSON이 일치한다. 자연 사라짐은 등장을 참조하며 CNN 결과를 복제하지 않았다.
- 최종 미디어 경로/시각, 대표 crop과 OSNet 원본 보존, MP4 9개/JPEG 8개 디코딩, 이벤트/CNN 큐 마무리·Worker 종료·토큰 폐기·백엔드 정상 종료(exit 0), 운영 DB/설정 파일 해시 불변을 확인했다. 초기 미확정 로컬 관측의 temp 자료는 기존 정책대로 보존한다.
- 보고서: `output/event_mvp_stage5_cnn_stage3_webcam_a/report.json`. 임시 진단 DB: 같은 폴더 `backend.db`. 미디어: `verification_media/event_mvp_stage5_cnn_stage3_webcam_a/AI_CCTV`. 이 경로들은 검증 산출물이며 제품 실행에 필요하지 않다.
- 이 검증은 단일 인물·단일 카메라 정상 흐름이다. 다른 사람 구분, 속성 정답 정확도, 두 실제 카메라/RTSP, 장시간·부하/메모리·지연 정량 측정은 수행하지 않았다. 시작 구간에 기존 녹화의 프레임 보정 한도 초과 로그가 있어 무손실/끊김 없는 동작을 보장하는 결과가 아니다.
- 운영 DB/계정/설정·기존 미디어·가중치 원본은 변경하지 않았다. Git 브랜치 변경·커밋·푸시는 하지 않았다.

## 최종 저장 계약

- 컬럼명: **cnn_result**. 초기 SQL NULL, person_appeared에만 결과 저장. 사라짐은 related_event_id로 등장 결과 참조.
- PATCH /api/v1/events/{event_id}/cnn-result 추가. 결과는 원래 영문 속성 라벨과 후처리한 한국어 age를 담는다. 기존 미디어 PATCH와 별개다.
- create_all은 기존 테이블에 컬럼을 추가하지 않으므로 2단계의 컬럼 전용 자동 백업/마이그레이션을 사용한다. 운영 DB 초기화 금지.
- 분석 실패 시 이벤트는 유지하고 NULL 및 안내/로그로 처리한다. NULL은 대기/미분석/실패를 구별하지 않는다. 소지품 검출 없음은 빈 배열이다.
- 종료: 영상/ReID/미디어 정리 → 마지막 등장 저장 확인 → CNN 제한 시간 마무리 → 결과 PATCH 포함 EventStore 마무리 → 로그아웃.
- 별도 테이블/상태 컬럼, 영속 큐, 무한 재시도, 자동 재시작 복구는 초기 MVP에서 제외한다.
