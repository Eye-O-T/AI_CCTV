# OSNet 특징 추출·공용 매칭·영상 표시 — 2~5단계

저장된 crop 5장→대표 벡터→공용 Manager/API 저장·카메라별 큐·영상 Local/Global 표시를 구현했다. 사용자 실촬영에서 단일 인물의 두 카메라 Global 1 연결을 확인했다. 다른 사람/동시 인물·지속 성능은 미검증이다. 단계별 작업과 인계는 [개발 계획](../docs/GLOBAL_PERSON_DEVELOPMENT_PLAN.md)을 읽는다.

## 실행 환경과 가중치 준비

- 기존 가상환경을 재사용한다. 이번 단계에서 패키지를 설치/업그레이드하지 않았다. 검증 조합은 Python 3.11.9, torch 2.5.1+cu121, torchvision 0.20.1+cu121이며 Pillow도 사용한다. 다른 환경은 호환되는 PyTorch/torchvision 조합을 준비해야 한다. 전체 AI 환경을 재설치할 필요는 없다.
- device=auto는 CUDA 가용 시 cuda:0, 아니면 CPU를 선택한다. CPU를 명시할 수도 있다. CUDA 실행 실패 시 조용히 CPU/다른 모델로 대체하지 않는다.
- 모델은 osnet_x1_0, MSMT17 combineall 학습 가중치다. 최초 계획의 Google Drive Model Zoo와 같은 파일명을 저자 계정의 [Hugging Face 배포](https://huggingface.co/kaiyangzhou/osnet)에서 고정 리비전으로 취득했다. 출처·파일명·해시는 config.py에 있다.
- 원본 소스는 [공식 저장소](https://github.com/KaiyangZhou/deep-person-reid)의 리비전 f8cd150fdf77e8d9e1ed143b7f308c2c609ded50에서 가져왔다. vendor/SOURCE.json과 vendor/LICENSE.deep-person-reid에 원본/추론용 수정본 해시 및 MIT 라이선스를 보존했다. 전체 torchreid 학습 패키지를 설치하지 않는다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m client_code.reid.download_weights
```

기본 가중치는 client_code/reid/weights/osnet_x1_0_msmt17_combineall.pth에 저장하며 실행 위치와 무관하다. 약 17.3MB이고 Git에서 제외한다. 다른 PC/새 checkout에서는 위 명령으로 준비한다. 같은 크기·해시의 파일이 있으면 네트워크 없이 재사용하며, 기존 파일이 다르면 보존하고 실패한다. 다운로드 임시 파일 검증 후 기존 파일을 덮어쓰지 않는 방식으로 게시한다. 추론 중 자동 다운로드는 하지 않는다.

기준 SHA-256:

```text
48df972f72887b95cf3b43b3a07c3a7d2398381aea0f9cae64a7ef11d512b727
```

## 호출 방법

저장소 루트에서 package 방식으로 사용할 수 있다.

```python
from client_code.reid.osnet_extractor import OSNetExtractor

# 초기화는 GUI/영상 스레드 밖에서 한 번 수행하고, 이후 Worker들이 공유한다.
extractor = OSNetExtractor(device="auto")
vector = extractor.extract(["crop_01.jpg", "crop_02.jpg", "crop_03.jpg",
                            "crop_04.jpg", "crop_05.jpg"])
```

현재 PyQt script 실행 방식에서는 `from reid.osnet_extractor import OSNetExtractor`도 지원한다. 입력은 서로 다른 이미지 경로 5개이며 반환은 길이 512의 list[float]다. 원본 사진을 수정하지 않는다. 특징 배치의 shape·유한성·norm과 최종 공통 벡터 형식을 검사한다. OOM·이미지/모델 오류는 OSNetError의 고정 code/메시지로 전달하며 원본 경로·내부 예외를 메시지에 넣지 않는다.

빠른 확인 명령(사진 경로는 실제 저장된 5장으로 지정):

```powershell
.\venv311\Scripts\python.exe -B -m client_code.reid --device auto crop_01.jpg crop_02.jpg crop_03.jpg crop_04.jpg crop_05.jpg
```

명령은 실행 장치·차원·norm·추출 시간을 출력한다. 전체 벡터와 이미지를 로그에 출력하거나 DB를 변경하지 않는다. 여러 카메라가 모델을 공유할 때 동일 extractor 인스턴스를 전달해야 하며, 모델 인스턴스를 자동으로 전역 생성하지 않는다. forward와 GPU 결과 복사를 잠금 안에서 처리한다.

## 검증과 제한

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/tests -p test_osnet_extractor.py -v
```

- 실제 고정 가중치를 사용하는 16개 테스트 통과. CPU 실제 추론, RGB 전처리, 개별 정규화 후 평균, 잘못된 특징/이미지/가중치, 특징 계층 누락, script/package import, 공유 forward 직렬화, 다운로드 검증·중단·기존 파일 보존을 확인했다. 테스트가 가중치를 자동 다운로드하거나 누락을 skip 성공으로 처리하지 않는다.
- 기존 저장 crop 두 묶음(각 5장)을 CPU와 CUDA에서 읽기 전용 추출했다. 512차원·norm≈1, CPU/CUDA 코사인 유사도 약 0.999994, 동일 장치 반복 결과 차이 0, 원본 파일 해시 불변을 확인했다. 결과는 프로젝트 루트 output/reid_stage2/report.json에 있다.
- GPU OSNet 단독 peak allocated 약 75.95MiB였다. GPU 첫 배치 약 683ms, 두 번째 묶음 약 38ms이며 모델 로딩/초기화 영향과 워밍업이 섞인 단발 관측이다. CPU는 검증 프로세스에서 스레드 수를 2로 제한한 값이다. 일반 성능·실시간 처리 보장으로 사용하지 않는다.
- 카메라를 실행하거나 YOLO와 동시 실행하지 않았다. 같은 사람/다른 사람 판별 정확도, 임계값 0.80, 카메라 간 매칭은 검증하지 않았다. 모델은 기준선이며 현재 PC에 가장 적합한 최종 모델이라고 확정하지 않는다.
- 1단계 백엔드 53개·2단계 추출 16개 결과는 재사용한다. 3단계에서는 백엔드/공통 벡터/추출 코드를 수정하지 않았고 운영 DB·backup/은 사용하지 않았다. 실제 카메라 큐 연결은 4단계다.

## 공용 GlobalPersonManager — 3단계

```python
from client_code.reid.api_client import GlobalPersonApiClient
from client_code.reid.global_person_manager import GlobalPersonManager
from client_code.reid.tasks import MatchTask, Observation

api = GlobalPersonApiClient(base_url, access_token)  # 기존 로그인에서 받은 값
manager = GlobalPersonManager(api)                  # 프로그램 전체 한 개
manager.start()                                    # 시작 조회·HTTP는 작업 스레드에서 수행
```

- 상태는 STARTING→READY, 실패 시 ERROR, 종료 시 STOPPING→STOPPED다. state/error_code를 읽을 수 있다. 테스트/일반 작업 스레드는 wait_until_ready(timeout)을 쓸 수 있지만 GUI는 대기하지 않는다.
- Observation에는 UUID v4 task_id, camera_id, execution_id(UUID v4), tracking_generation(0 이상), local_person_id, 시간대가 포함된 observed_at을 넣는다. task_id는 새 관측마다 한 번 생성하고 재시도에 재사용한다.
- 실제 유효 관측을 begin_observation으로 등록하고 MatchTask(observation, vector)를 submit한다. 벡터는 공통 float32 규칙으로 검증·고정하며 repr에 출력하지 않는다. submit=False는 미준비/오류/종료 관측/중복/큐 포화를 뜻한다. 호출자가 상태와 기존 제출 여부를 확인하고 상한 내에서 처리해야 한다.
- take_results()는 현재 관측에 대한 MatchResult 목록을 반환한다. 결과에는 관측 식별 정보·글로벌 ID·new/existing·최고 유사도(후보 없음은 None)가 있다. 임계값은 0.80 기본이며 설정 가능하다. 프레임별 요청은 없다.
- 종료 관측은 end_observation(task_id), 카메라는 end_camera(camera_id, execution_id)로 해제한다. 실행 ID는 Observation의 정규화된 hex 값을 사용한다. 카메라 재시작의 늦은 결과를 새로운 관측으로 등록하지 않는 책임은 4단계 생산자에도 있다.
- 입력/결과 큐는 각각 기본 32개다. 생산자 submit은 비차단이며 결과 소비가 늦어 큐가 차면 Manager만 대기한다. 영상 수신과 GUI를 멈추지 않는다.
- 연결/5xx/잘못된 응답은 현재 결정을 보존한 ERROR 상태다. retry_pending()으로 같은 요청을 명시적으로 재시도한다. 다음 작업을 먼저 처리하거나 매칭을 다시 계산하지 않는다. 같은 카메라에서 관측이 끝나도 불확실한 저장은 확인하고 후보에 반영하되 종료 연결은 되살리지 않는다.
- 401 등 재시도 불가 오류는 같은 Manager로 재로그인·재전송하지 않는다. 종료 후 새 인증/후보 복원으로 시작하며 이전 미확정 작업은 버린다. 일반 사전 Access 갱신은 caller가 api.set_access_token으로 안전한 사본을 전달할 수 있다. 자동 갱신이나 Qt SessionManager 접근은 하지 않으며 4단계에서 신호를 연결한다.
- 종료는 request_stop() 후 작업 스레드 밖의 비동기 종료 관리에서 join 완료를 확인한다. 진행 중 HTTP는 기본 5초 connect/read 제한을 사용하므로 종료가 즉시 끝난다고 가정하지 않는다. Manager 종료는 HTTP 클라이언트와 토큰 사본도 폐기한다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/tests -p test_global_person_manager.py -v
```

3단계 19개 통과. 테스트 벡터의 매칭 정책·큐·수명·재시도와 실제 임시 DB/HTTP의 신규·기존·후보 복원·커밋 후 응답 유실 재전송을 확인했다. 이 결과는 실제 카메라 재식별 정확도나 로그인 UI 연계 검증이 아니다.

## 카메라·로그인 연결 — 4단계

- 실제 앱에서는 CameraRuntimeManager가 로그인된 SessionManager의 주소/Access 토큰 사본으로 ReIDPipeline을 준비한다. 첫 AI 카메라 시작에서 백그라운드로 모델·후보를 복원한다. 영상 수신을 모델 준비나 HTTP 응답 때문에 대기시키지 않는다.
- VideoWorker별 CropWorker의 완료 결과를 OSNetWorker의 입력 큐(16개)에 전달한다. 큐가 차면 영상 쪽 보류를 16개까지만 유지하고 초과 관측은 안내 후 제외한다. OSNetWorker는 공유 extractor 하나를 사용하고 공용 Manager로 대표 벡터를 한 번 제출한다.
- 관측별 UUID, 카메라 실행 UUID, 추적 세대, 로컬 ID와 첫 품질 통과 crop의 실제 UTC 촬영 시각을 전달한다. 폴더명 기준 first_seen은 이전과 같은 로컬 시각이며 촬영 시각을 대신하지 않는다.
- 추적 종료/초기화·카메라 STOP은 해당 관측과 작업만 해제한다. 한 카메라가 종료 대기 중이어도 다른 카메라는 수신한다. 이미 진행 중인 파일 저장·모델 추론은 안전하게 마무리하되 종료 관측에 결과를 재연결하지 않는다. 새로운 실행은 이전 worker 정리 전 같은 슬롯을 재사용하지 않는다.
- 사전 토큰 갱신은 GUI에서 pipeline.set_access_token으로 전달한다. 401·세션 만료·로그아웃은 기존 앱 종료 흐름에 연결하여 카메라/OSNet/공용 Manager의 정리를 확인한 뒤 토큰 폐기를 진행한다. 작업 스레드가 Qt SessionManager를 직접 호출하지 않는다.
- 모델/입력/저장 오류는 영상 수신을 유지하며 안내한다. 저장 오류의 같은 실행 재확인은 runtime.retry_reid() 메서드로 가능하지만 전용 재시도 UI 버튼은 아직 없다. 일반 사용자에게는 로그아웃 후 재로그인으로 새 pipeline을 만드는 경로를 안내한다.
- 결과는 VideoWorker.global_person_ids, reid_ready 및 CameraRuntimeManager.global_person_ready로 전달한다. 이 단계에서는 화면/바운딩박스 글로벌 ID 표시를 추가하지 않았다.

가중치를 준비한 뒤 일반 앱 실행(기본 DB/실행 기록이 생성되며 계정이 없으면 최초 회원가입 필요):

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe client_code/main.py
```

4단계의 이번 검증은 기본 앱/운영 DB를 실행하지 않고 임시·합성 환경에서 수행했다. 신규 14개·crop 19개·인증 전환 25개가 통과했다. 추가 카메라 실행/멀티카메라 회귀는 25개 중 22개 통과이며 기존 CAP_DSHOW 영상 파일 입력 3개가 실패했다. 소유 입력 코드는 수정하지 않았다. 자세한 실패 이름은 개발 계획과 기존 클라이언트 README 기록을 따른다.

기존 crop 두 묶음으로 실제 CUDA 모델·OSNetWorker 두 개·공용 Manager·임시 HTTP/DB 전체 연결도 확인했다(output/reid_stage4/report.json). 원본 사진은 보존했다. 실제 두 장치 영상·YOLO 동시 실행·동일인 정답 정확도와 임계값 튜닝은 5단계에서 확인한다.

## 글로벌 ID 영상 표시 — 5단계

프로그램을 완전히 종료 후 다시 실행하면 영상의 첫 줄은 Local:1 Global:12, 둘째 줄은 FULL/PARTIAL Det:0.97로 표시된다. Global:--는 아직 연결된 글로벌 ID가 없다는 뜻이다. Det는 YOLO 탐지 신뢰도이고 코사인 유사도가 아니다. 전신으로 판정되지 않아도 앞서 같은 관측에서 확정한 글로벌 ID는 유지한다. 추적 종료/초기화·재시작 후에는 이전 ID를 표시하지 않는다.

매칭 완료 때 터미널의 [ReID] 로그에서 camera/local/global/mode/similarity/elapsed_ms를 확인할 수 있다. elapsed_ms는 5장 저장 완료 결과 수신부터 글로벌 결과 반영까지이며 crop 수집 대기 시간은 제외한다. 최초 후보가 없으면 similarity=none이다. 벡터·토큰 원문은 출력하지 않는다.

라벨 4개·pipeline 14개·crop 19개로 이번 37개 검증을 통과했고 합성 라벨도 시각 확인했다. 예시는 output/reid_stage5/label_preview.png다. 사용자 실촬영에서는 카메라 1/2의 Local 1이 모두 기존 Global 1로 연결됐고 유사도 0.936/0.904, 결과 반영 328/329ms가 보고됐다. 유사도는 저장된 대표 벡터 기준이며 정확도 비율이 아니다. 재등장·재실행·다른 사람·동시 두 사람·지속 FPS/GPU 메모리는 미검증이고 임계값 0.80은 변경하지 않았다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m unittest discover -s client_code/tests -p test_reid_labels.py -v
```
