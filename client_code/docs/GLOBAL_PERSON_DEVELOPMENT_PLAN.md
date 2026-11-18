# 멀티카메라 글로벌 인물 ID 개발 계획

- 작성일: 2026-11-18.
- 0단계: 계약·모델·실행 설정 문서화 완료. 코드·DB·모델 설치/다운로드는 진행하지 않았다.
- 1단계: 글로벌 인물 모델·인증된 3개 API 구현 및 임시 DB 검증 완료. 실제 DB/backup/은 변경하지 않았고 OSNet·Manager·카메라 연결은 미구현이다.
- 2단계: 공식 OSNet 소스·저자 배포 가중치 취득, 대표 벡터 추출, 16개 테스트 및 기존 crop의 CPU/CUDA 추출 검증 완료. 실행 방법은 [reid/README.md](../reid/README.md)를 읽는다. OSNetWorker·Manager·카메라 큐 연결은 아직 미구현이다.
- 3단계: 독립 공용 GlobalPersonManager·작업 DTO·HTTP 클라이언트 구현, 19개 테스트 및 임시 백엔드 HTTP 연동 완료. 실제 OSNetWorker·카메라 큐·화면 연결은 4~5단계다.
- 범위: 현재 최대 두 카메라의 CropWorker 결과에서 OSNet 대표 벡터를 만들고 공용 GlobalPersonManager로 ID를 연결·저장·표시한다. 이벤트·CNN·Flutter·알림·미디어 API는 제외한다.
- 소유 문서: [DB 스키마](BACKEND_SCHEMA.md), [API 계약](API_SPEC.md#글로벌-인물-api--멀티카메라-reid-mvp). 이번 단계는 global_person_manage만 추가 대상으로 삼고 events는 후속 설계로 유지한다.

## 1. 현재 코드와 확인한 환경

- VideoWorker는 카메라별 execution_id(UUID)를 만들며, CropWorker 결과를 받아 5장 저장 완료 로그를 출력한다. OSNet 연결은 아직 없다.
- CropWorker는 결과에 generation, person_id, paths, error를 전달한다. 관측 종료 후 같은 로컬 ID 재사용과 실행 재시작을 구분할 추가 식별 정보가 필요하다.
- 전신 검사·크기/선명도 검사·0.5초 간격·첫 5장 저장 정책과 기존 사용자 파일을 보존한다.
- 2026-11-18 읽기 확인: Python 3.11 환경의 torch 2.5.1+cu121, torchvision 0.20.1+cu121. torch.cuda.is_available()=True, GPU는 RTX 3050 Laptop 4096MiB, 드라이버 546.92다.
- torchreid 설치와 OSNet 가중치 파일은 조사한 venv311/site-packages 및 client_code 범위에서 발견하지 못했다. 0단계에서는 새 패키지 설치·가중치 다운로드·OSNet 추론·장치 실행 검증을 하지 않았다.

## 2. 확정한 OSNet 프로파일

| 항목 | 0단계 결정 |
| --- | --- |
| 아키텍처 | 공식 deep-person-reid의 osnet_x1_0 |
| 가중치 | 공식 Model Zoo의 Cross-domain ReID → MSMT17 (combineall=True) → osnet_x1_0 |
| 공식 배포 파일명 | osnet_x1_0_msmt17_combineall_256x128_amsgrad_ep150_stp60_lr0.0015_b64_fb10_softmax_labelsmooth_flip_jitter.pth |
| 로컬 경로 | client_code/reid/weights/osnet_x1_0_msmt17_combineall.pth (취득 완료, Git 제외) |
| 입력 | 저장된 JPEG 5장, PIL로 RGB 변환, H×W=256×128로 직접 Resize |
| 리사이즈 | torchvision Resize, BILINEAR, antialias=True를 명시. 추론 중 random crop/flip 없음 |
| 픽셀 정규화 | ToTensor로 [0,1], mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225] |
| 출력 | eval 모드의 분류기 이전 특징, 이미지당 512차원 |
| 추론 | eval + inference_mode, float32, 5장 한 배치. 초기 FP16/학습/파인튜닝 없음 |
| 대표 벡터 | 각 특징 L2 정규화 → 5개 산술평균 → 다시 L2 정규화 |
| 유효성 | 유한한 비영벡터, 최종 512차원, norm 허용 오차 0.001 |
| 저장·전송 | DB: little-endian float32 2048바이트 / JSON: 숫자 배열 512개 |
| 비교 | 코사인 유사도. 정규화 벡터 내적으로 계산 |
| 초기 임계값 | 0.80을 구현·측정 시작값으로 사용. 정확도 보장값이 아니며 5단계 실촬영으로 조정 |

표준 x1.0을 기준선으로 선택한다. 공식 학습 가중치의 평가 거리 표기는 euclidean이지만 이번에는 L2 단위 벡터의 코사인 비교로 통일한다. 단위 벡터에서는 제곱 유클리드 거리=2−2×코사인 유사도이므로 후보 순위는 대응한다. 유사도 임계값은 거리 임계값을 그대로 가져오지 않고 실제 촬영으로 정한다.

이 선택은 현재 PC에서 최고 성능을 검증한 결과가 아니다. 다른 PC에서도 같은 모델과 전처리를 사용하고 실행 장치만 선택한다. 실제 crop 정확도·추론 지연·YOLO 동시 메모리를 측정한 뒤 필요하면 AIN x1.0 또는 경량 모델과 비교한다. 모델을 변경할 때는 기존 벡터 혼합 금지 규칙을 적용한다.

2단계에서 저자 배포 파일을 취득하고 SHA-256을 확인했다. config.py와 vendor/SOURCE.json에 가중치/원본 소스 리비전을 기록했고 extractor 초기화 시 파일 크기·해시를 검사한다. ImageNet 사전학습 또는 무작위 초기화로 조용히 대체하지 않는다. 특징 추출 계층의 누락/불일치는 오류로 처리하고 분류기만 명시적으로 제외한다.

공식 OSNet 추론에 필요한 코드만 출처·라이선스를 보존해 reid/vendor 아래에 두고 기존 PyTorch/torchvision을 재사용했다. 전체 학습용 torchreid 설치나 환경 업그레이드는 하지 않았다. 사용법·실제 취득 경로·제한은 reid/README.md를 따른다.

공식 근거:

- [Model Zoo](https://kaiyangzhou.github.io/deep-person-reid/MODEL_ZOO)의 MSMT17 combineall x1.0 배포 항목, [선택한 가중치](https://drive.google.com/file/d/1IosIFlLiulGIjwW3H8uMRmx3MzPwf86x/view).
- [FeatureExtractor 소스](https://github.com/KaiyangZhou/deep-person-reid/blob/master/torchreid/utils/feature_extractor.py)의 RGB·리사이즈·픽셀 정규화와 배치 추출 방식.
- [OSNet 소스](https://github.com/KaiyangZhou/deep-person-reid/blob/master/torchreid/models/osnet.py)의 512차원 특징 및 eval 출력.

## 3. 스레드·모델·실행 장치

```text
카메라 A: VideoWorker → CropWorker → OSNet 입력 큐 → OSNetWorker ─┐
                                                               ├→ 공용 결과 큐
카메라 B: VideoWorker → CropWorker → OSNet 입력 큐 → OSNetWorker ─┘
                                                                      ↓
                                                          GlobalPersonManager 한 개
                                                                      ↓
                                                      백엔드 글로벌 인물 API → DB
                                                                      ↓
                                                          관측별 글로벌 ID 표시
```

- 카메라별 OSNetWorker 스레드는 유지하되 초기 모델 인스턴스는 PC 프로세스에 한 개 공유한다. 4GB GPU와 두 YOLO를 고려해 OSNet 모델을 카메라마다 중복 적재하지 않는다.
- extractor 내부 잠금으로 공유 모델의 GPU 추론을 한 번에 한 배치만 실행한다. 영상 수신과 이미지 준비는 비동기이며 OSNet 추론 직렬화와 Manager의 결정 직렬화는 다른 책임이다.
- 기본 device=auto: CUDA 가용 시 cuda:0, 없으면 CPU를 선택하고 실제 장치를 표시한다. cpu 명시 실행도 지원한다. 실행 중 CUDA 오류/OOM은 해당 ReID 기능 오류로 표시하며 자동 모델 교체·반복 CPU 재추론을 하지 않는다. 영상 수신은 유지하고 재시작으로 복구한다.
- 모델 로딩·추론·HTTP 응답 대기는 GUI/영상 수신 스레드에서 수행하지 않는다. 두 카메라 GPU 메모리·추론 시간·큐 적체는 4~5단계에서 측정하며 0단계 CUDA 확인을 성능 검증으로 간주하지 않는다.

## 4. 작업 식별·큐·수명

- 관측 식별은 (camera_id, execution_id, tracking_generation, local_person_id)와 관측 시작마다 새로 부여하는 task_id(UUID v4)를 함께 사용한다. 같은 실행·세대 안에서 로컬 ID가 재등장해도 새 관측에는 새 task_id를 준다.
- task_id는 관측 시작에 한 번 생성하여 crop 저장 결과·OSNet 입력·결과·신규 등록 request_id까지 재사용한다. 재시도마다 새 UUID를 만들지 않는다. 이 값은 DB 컬럼을 추가하지 않고 PC 및 단일 백엔드 실행의 메모리에 보관한다.
- Crop 완료 작업: 위 식별 정보 + crop_paths[5] + observed_at. observed_at은 대표인 첫 저장 crop의 실제 촬영 시각을 UTC로 보존한다. 현재 결과에 시각·관측 토큰이 없으므로 4단계에서 확장한다. 폴더명에 쓰는 first_seen이나 파일 mtime을 대체값으로 사용하지 않는다.
- OSNet 결과: 식별 정보 + 대표 벡터 + observed_at 또는 실패 코드. event_id는 사용하지 않는다. 글로벌 ID를 DB에 보관하되 카메라별 로컬↔글로벌 연결은 메모리에만 둔다.
- 큐 초기 상한은 카메라별 입력 16, 공용 결과 32, 저장 완료 보류 작업 카메라별 16으로 한다. 중복 task_id를 막고 결과 큐 대기는 OSNetWorker에서만 수행한다. 소비 지연 시 영상/GUI 스레드를 대기시키지 않는다.
- 보류 상한까지 찼다면 새 ReID 작업 접수를 멈추고 안내한다. 무한 누적·조용한 성공 처리 대신 누락을 알려주며 해당 관측은 이번 분석에서 제외할 수 있다. 영속 작업 복구는 구현하지 않는다.
- 추적 종료·세대 변경·카메라 STOP 시 현재 관측 연결을 해제하고 대기 작업을 취소한다. 이미 진행 중인 작업 결과도 유효성을 재확인하며 종료 관측이면 버린다. 이미 커밋된 글로벌 인물은 삭제하지 않고 종료 관측을 다시 활성화하지 않는다.
- 로그인/Manager 초기화 시 후보 전체 복원 후 새 매칭을 시작한다. 서버 재시작·로그아웃에는 이전 미확정 작업과 request_id를 비우며 새 인증 이후 자동 재전송하지 않는다.

## 5. 매칭·DB 경계

- 공용 Manager 하나가 입력을 순차 처리한다. 현재 같은 카메라에서 다른 인물이 사용 중인 ID를 제외한 뒤 최고 유사도를 선택한다. 임계값 이상 기존, 미만 또는 후보 없음 신규, 동점이면 작은 ID다.
- 최초 대표 벡터는 고정한다. 기존 인물은 마지막 연결 관측 시각만 API로 갱신한다. 프레임별 DB 갱신·상태 폴링은 없다.
- 신규 인물은 POST /global-persons, 기존 인물은 PATCH /global-persons/{id}/last-seen으로 저장한다. Manager는 저장 성공을 확인한 뒤 메모리를 반영하고 다음 매칭을 판단한다.
- 신규 등록 응답 유실은 같은 request_id/본문 재전송으로 확인한다. 백엔드 단일 실행 메모리의 키 기록으로 같은 요청 중복을 막는다. 서버 재시작을 넘는 영속 재시도 보장은 이번 범위에서 제외한다. 상세 오류·DTO는 API_SPEC.md를 따른다.
- 실패 시 다음 매칭 판단은 멈추고 ReID 오류를 안내하되 영상 수신은 유지한다. 추론 실패를 신규 인물로 저장하지 않는다.
- 모델/가중치/전처리를 바꾸면 기존 벡터를 비교 대상으로 섞지 않는다. 기존 기록 보존·재추출 또는 명시적으로 승인한 초기화 중 하나를 정한 뒤 변경한다. 이번 단계는 모델 버전 컬럼/추가 테이블을 만들지 않는다.

## 6. 예정 파일 구조

```text
client_code/
  reid/
    config.py                   # 프로파일·장치·임계값·큐 상한
    tasks.py                    # 관측/작업/결과 데이터
    osnet_extractor.py          # 모델 로딩·공유 잠금·대표 벡터
    global_person_manager.py    # 공용 큐·결정·저장 확인
    api_client.py               # 기존 인증과 연계한 HTTP
    weights/                    # 2단계에서 취득, 가중치는 Git 제외
  workers/osnet_worker.py        # 카메라별 큐 소비 스레드
  backend/
    models/global_person.py
    schemas/global_person.py
    repositories/global_person_repository.py
    services/global_person_service.py
    routers/global_persons.py
```

reid/와 파일은 해당 구현 단계에 생성한다. 1단계 공통 벡터 규칙은 client_code/feature_vector.py에 두었다. 표준 라이브러리 struct/math만으로 float32 변환과 유한성·norm을 검사하므로 후속 추론 코드에서도 재사용할 수 있다. 백엔드가 PyQt·OSNet·YOLO를 import하지 않는 경계를 유지한다.

## 7. 단계·검증·다음 작업

| 단계 | 작업 | 완료 기준 | 현재 상태 |
| --- | --- | --- | --- |
| 0 | 이벤트 없는 API·모델·장치·큐 계약 | 소유 문서 일치, 환경 확인, 남은 실측 구분 | 문서 정리 완료 |
| 1 | 글로벌 인물 모델·인증된 3개 API | 임시 DB 등록/조회/시각 갱신, 512차원 BLOB 왕복, 실패/중복/동시 요청, 기존 인증·카메라 회귀 | 완료: 전체 53개 통과 |
| 2 | 실제 OSNet 추출 | 공식 파일·소스 리비전·해시 기록, 실제 5장→512 벡터, 입력/가중치 실패 검증 | 완료: 16개 통과, CPU/CUDA 저장 crop 검증 |
| 3 | 공용 Manager | 테스트 벡터로 임계값/동점/동시 관측 제외, 등록·저장 유실 재시도, 후보 복원 | 완료: 19개 통과, 임시 HTTP 연동 |
| 4 | 두 카메라 큐·수명 연결 | task_id 전달, STOP/재시작/추적 종료의 늦은 결과 차단, 큐 상한, 다른 카메라 유지 | 미구현 |
| 5 | 화면 표시·실촬영 | A→B 동일 인물/다른 사람/동시 인물, 재실행 복원, 추론 시간·GPU 메모리·큐 적체 기록, 임계값 조정 | 미구현 |

다음 작업은 4단계 두 카메라 OSNetWorker·큐·관측 수명 연결이다. 3단계 완료는 4단계 구현 승인을 뜻하지 않는다. events 생성, 운영 DB 초기화, 커밋·푸시는 포함하지 않는다. 단계별 검증 결과를 이 문서에 기록하고 실패 원인이 없는 검사를 반복하지 않는다.

## 8. 1단계 구현·검증·인계 (2026-11-18)

- 모델: global_person_manage의 PK AUTOINCREMENT, 2048바이트 BLOB CHECK, UTC 시각, 최초 created_at=updated_at을 구현했다. 서버 시작의 create_all에 등록되어 다음 정상 실행에서 없는 테이블을 만든다. 기존 테이블의 구조 변경/마이그레이션은 하지 않는다.
- API: GET/POST /api/v1/global-persons, PATCH /api/v1/global-persons/{global_person_id}/last-seen. 별도 DTO와 기존 Access 인증을 적용했다. 대표 벡터 갱신·삭제·이벤트 API는 추가하지 않았다.
- Service: 요청별 재시도 기록은 사용자 ID+UUID v4 키로 메모리에 유지하며 등록 잠금과 커밋 성공 후 기록으로 중복을 방지한다. 같은 키/다른 내용은 409다. 마지막 관측은 BEGIN IMMEDIATE 트랜잭션으로 max 갱신하며 최초 벡터와 updated_at을 유지한다.
- 검증: 백엔드 전체 **53개 통과**(기존 38개 + 신규 15개). 새 테스트는 tests/test_global_person_api.py다. 임시 DB에서 벡터 왕복, 잘못된 차원/타입/NaN/무한대/영벡터, UUID/시각 오류, 오류 입력 미노출, 인증 누락/만료/로그아웃/Refresh 오용, 등록·갱신 동시성, flush/commit 실패 롤백, 응답 유실을 가정한 재시도, 재시작 후 토큰 무효·후보 복원을 확인했다.
- 임시 DB의 기존 계정·카메라·실행 테이블을 보존하며 새 테이블만 추가되는 것을 확인했다. 운영 DB 없이 자동 테스트가 계정을 만들고 로그인한다.
- 실제 로컬 HTTP: 임시 DB와 127.0.0.1 임시 포트의 Uvicorn으로 인증 거부→가입/로그인→등록 201→재전송 200→마지막 관측 갱신→목록/벡터 조회와 OpenAPI 경로를 확인했다. OSNet 실제 특징, PyQt 연결, 카메라 실행, 운영 DB/API 또는 Swagger 화면 조작 검증은 아니다.
- 테스트 과정의 Windows SQLite 파일 잠금은 테스트 연결을 명시적으로 닫도록 수정했다. 기존 인증 테스트의 예상 테이블/경로 목록은 새 모델/API를 포함하도록 갱신했다. 기존 Starlette/httpx 폐기 예정 경고는 유지하며 패키지 업그레이드는 하지 않았다.
- 제한: 등록 재시도 이력은 단일 서버 실행의 메모리뿐이다. PC 재시작/재로그인 동작은 아직 구현 전이며 서버 재시작 뒤 이전 미확정 요청을 재전송하지 않는 계약을 3~4단계에서 적용해야 한다.
- 실제 기본 data 폴더에는 테스트로 ai_cctv.db를 만들지 않았다. 사용자 계정은 아직 가입하지 않은 상태로 둘 수 있다. 다음 실제 프로그램 실행은 DB 생성·서버 실행 기록을 남기므로 검증과 구분한다.
- 다음 세션: AGENTS.md → 이 문서 → API_SPEC.md/글로벌 인물 절 → BACKEND_SCHEMA.md/7절 → backend/README.md를 읽고 Git 상태를 확인한다. 1단계 테스트를 불필요하게 반복하지 말고 2단계 모델 취득·해시/리비전 기록·5장 추출부터 진행한다. 실제 글로벌 매칭과 화면 연결은 3~5단계다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/backend/tests -v
```

## 9. 2단계 구현·검증·인계 (2026-11-18)

- 구현 파일: reid/config.py, osnet_extractor.py, download_weights.py, __main__.py, vendor/osnet.py 및 출처/라이선스. 초기화 시 고정 가중치를 검증하고 5장 배치의 특징→개별 L2→평균→L2를 수행한다. 이미지/추론 오류는 고정 code로 반환하며 원본을 수정하지 않는다.
- 상류 리비전: f8cd150fdf77e8d9e1ed143b7f308c2c609ded50. 원본 소스 SHA-256은 c7c1c29187d6330f859c91da229271531920464c7011aec13842a086b2263cae다. x1.0 계층만 남기고 네트워크 다운로드/다른 모델 factory를 제거했으며 MIT 라이선스를 보존했다.
- 실제 가중치 취득: Model Zoo와 동일한 MSMT17 combineall 파일명을 저자 계정 kaiyangzhou/osnet의 Hugging Face 리비전 a5c5cc037c24235cda3b21085b93ad77c9616224에서 받았다. Google Drive 파일을 직접 내려받은 것으로 표현하지 않는다. 17,273,805바이트, SHA-256=48df972f72887b95cf3b43b3a07c3a7d2398381aea0f9cae64a7ef11d512b727이다. 다운로더는 검증 후 게시하며 기존 다른 파일을 덮어쓰지 않는다.
- 테스트: test_osnet_extractor.py의 16개 통과. 실제 가중치 CPU 추론·재현성·사진 보존, 전처리, 잘못된 입력/특징/가중치와 계층 누락 거부, 공유 forward 잠금, 다운로드 실패·부분파일 방지, 현재 PyQt script import를 확인했다.
- 실제 저장 crop: output/camera_2 및 camera_3의 2026-11-18 자료 두 묶음(총 10장)을 CPU/CUDA에서 읽었다. 512차원·norm≈1, CPU/CUDA 코사인 약 0.999994, 같은 장치 반복 차이 0, 원본 파일 해시 불변이다. 로컬 결과는 output/reid_stage2/report.json이며 Git 제외다. GPU 메모리·첫 배치 시간은 reid/README의 제한과 함께 해석한다.
- 기존 백엔드 53개 결과를 재사용하며 이번 단계에서 재실행하지 않았다. 백엔드 코드·DB·백업·CropWorker·VideoWorker·화면은 변경하지 않았다. 별도 OSNetWorker/큐/Manager/매칭 정확도/YOLO 동시 실행 검증도 아직 없다.
- 다음 세션은 이 문서와 reid/README를 먼저 읽고 3단계 공용 큐·후보 복원·유사도/동점/동시 관측 제외·API 저장 확인을 구현한다. 입력 대표 벡터는 list[float]이며 extractor 하나를 후속 카메라별 Worker에 공유한다. task_id·STOP/추적 종료 연결과 실제 UI는 4~5단계다. 가중치는 Git 제외이므로 새 PC에서는 준비 명령을 실행해야 한다.

## 10. 3단계 구현·검증·인계 (2026-11-18)

- 구현 파일: reid/tasks.py의 불변 Observation/MatchTask/MatchResult, api_client.py의 동기 HTTP/응답 검증, global_person_manager.py의 비 daemon 공용 작업 스레드. torch·PyQt·YOLO 없이 import되며 GUI/영상 스레드에서는 submit 등 짧은 메서드만 호출한다.
- 시작 시 전체 후보를 검증·복원해야 READY가 된다. 실패를 빈 목록으로 바꾸지 않는다. 최대 32개 입력/결과 큐를 사용하고 submit은 즉시 성공/거부를 반환한다. 중복 task_id, 종료 관측, 큐 포화 시 접수를 거부하며 무한 보류하지 않는다.
- 매칭은 정확한 코사인 유사도로 최고 후보를 선택한다. 임계값 이상 기존, 그 외 신규, 동점은 작은 ID다. 같은 카메라의 다른 활성 관측이 사용하는 ID는 후보에서 제외한다. 저장 확인 뒤에만 후보·연결·결과를 반영한다.
- 네트워크/5xx/잘못된 응답은 ERROR로 멈추고 현재 결정·요청 ID를 보존한다. retry_pending으로 같은 저장 요청만 재확인하며 뒤의 큐 작업을 먼저 판단하지 않는다. 401/404/409/422/리다이렉트 등은 임의 자동 재시도하지 않는다.
- 401·서버 재시작·재로그인에서는 기존 Manager를 종료하고 이전 미확정 작업을 폐기한 뒤 새 인증·후보 복원으로 시작한다. API 클라이언트는 Qt SessionManager를 호출하지 않는다. 같은 로그인 실행의 사전 토큰 갱신은 GUI 쪽에서 set_access_token으로 전달할 수 있다. 실제 갱신/로그아웃 신호 연결은 4단계다.
- begin_observation으로 관측 등록 후 MatchTask를 submit한다. 종료 시 end_observation, 카메라 정리 시 end_camera를 호출한다. 동일 로컬 키의 새 task_id는 이전 관측 연결을 대체한다. 4단계에서는 실제 관측이 유효한지 확인한 crop 완료 작업만 등록해야 한다.
- HTTP 진행 중 관측이 끝나도 이미 저장된 글로벌 인물은 후보에 반영하여 DB/메모리를 맞춘다. 종료 관측은 재활성화하지 않고 take_results도 종료된 결과를 걸러낸다. 저장 응답이 불확실한 동안에는 관측이 끝나도 확인 전 다음 신규 판단을 하지 않는다.
- 종료는 request_stop 후 join으로 확인한다. 오류 재시도 대기는 즉시 깨우고, 진행 중 HTTP는 설정한 제한 시간 안에서 끝난 뒤 닫는다. API 토큰 사본·큐·연결·후보 메모리를 정리한다. 호출자는 GUI 스레드에서 blocking join을 하지 않아야 하며 4단계 실행 관리자가 비동기 종료를 연결한다.
- 검증: test_global_person_manager.py의 **19개 통과**. 테스트 벡터의 신규/기존·임계값 경계·동점·동시 관측 제외·연결 해제·같은 로컬 키 재등장·큐 포화/중복 거부·늦은 결과·후보 초기화 실패·저장 응답 유실/재시도·오류 중 종료를 확인했다. 잘못된 벡터/응답·HTTP 오류/리다이렉트·비밀값 미노출도 확인했다.
- 실제 로컬 HTTP: 임시 DB·임시 Uvicorn 포트에 테스트가 가입/로그인한 뒤 요청했다. 신규→다른 카메라 기존 연결→새 Manager의 후보 복원과, 실제 신규 INSERT 응답 수신 직후 유실을 모사한 같은 요청 재시도에서 DB 인물이 중복되지 않음을 확인했다.
- 제한: 테스트 벡터로 매칭 제어를 확인한 것이며 실제 OSNet 인식 정확도·두 카메라 영상/YOLO 실행·UI·토큰 갱신 연계는 검증하지 않았다. 기본 DB/backup/은 사용하지 않았고 백엔드·OSNet 추출 코드는 수정하지 않았다. 1단계 53개·2단계 16개 결과는 재사용한다.
- 다음 세션은 AGENTS.md → 이 문서 → reid/README.md → 실제 Manager/클라이언트/작업 DTO를 읽는다. 4단계에서 CropWorker 결과의 task_id·execution_id·실제 crop 시각을 보존하고, 공유 extractor·카메라별 OSNetWorker를 연결하며 카메라 중지/추적 종료/로그아웃 정리와 큐 포화 처리를 구현한다. 5단계 최종 화면·실촬영과 혼동하지 않는다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/tests -p test_global_person_manager.py -v
```
