# 인상착의 CNN 단독 추론

2026-11-20: 1단계 완료. 기존 crop 한 장을 분석해 JSON을 반환한다.
2단계에서 백엔드 cnn_result 컬럼/API를 추가했고, 3단계에서 공용 CNNWorker→EventCoordinator→EventStore 자동 저장과 종료를 연결했다. 현재 관제 프로그램은 등장 저장 성공 후 대표 crop 한 장을 자동 분석한다. 결과는 DB/이벤트 JSON으로 조회하며 인상착의 전용 화면은 추가하지 않았다.
CNN 수정/실행 전 [개발 계획](../docs/CNN_APPEARANCE_DEVELOPMENT_PLAN.md)을 읽는다.

## 가중치와 모델

- 경로: `client_code/appearance/weights/par_serving_exp3.pt`.
- 사용자 제공 Downloads 원본을 이름/내용 그대로 복사했다. 원본은 보존한다.
- 크기: 85,373,069바이트.
- SHA-256: `e3ec03c516ab6429dada23b285def0509478332028180361bc9ee19404033c69`.
- 기존 `.gitignore`의 `*.pt` 규칙으로 Git에서 제외한다. 다른 PC에서는 같은 파일을 별도 배치한다.
- ResNet34 + 8개 Linear head, 42개 클래스. `weights_only=True`로 bundle을 읽고 전체 state_dict를 strict 로딩한다. 다른 가중치/무작위 모델로 대체하지 않는다.
- Python 3.11, 기존 torch 2.5.1+cu121, torchvision 0.20.1+cu121, Pillow를 재사용한다. 추가 설치/업그레이드는 하지 않았다.

## 입력과 후처리

- 이미지 한 장을 PIL RGB로 읽는다. H×W=256×128로 직접 Resize(BILINEAR, antialias=True), ToTensor, mean `[0.485, 0.456, 0.406]` / std `[0.229, 0.224, 0.225]` 정규화를 적용한다.
- `eval()` + `inference_mode()`, float32. `auto`는 CUDA가 있으면 CUDA, 없으면 CPU다. 명시한 CUDA가 없으면 오류를 반환한다.
- 단일 선택 gender/age/hat/sleeve/lower_type: softmax 후 argmax.
- 다중 선택 upper_color/lower_color/carrying: sigmoid 후 bundle의 display_rules 적용.
- 상의 색상: 최고 점수 색상 + 0.7 이상 색상. 하의 색상: 최고 점수 한 개(threshold=null). 소지품: 0.6 이상만 선택하며 없으면 빈 배열. 배열은 bundle 라벨 순서다.
- 나이: 먼저 원래 5개 클래스 중 하나를 선택한다. `<=15`는 Male→소년/Female→소녀, `16-30`/`31-45`는 청년, `46-60`/`61+`는 장년으로 변환한다. 확률 합산/재학습/가중치 수정은 하지 않는다.
- bundle에는 학습/서빙 Python 코드가 없다. ResNet34+head는 strict 로딩과 실제 추론으로 확인했다. resize 보간법 및 활성화/표시 규칙 해석은 현재 구현 선택이며 원래 학습·서빙 코드와의 완전한 수치 일치는 확인하지 않았다.
- 파일 부재/손상, 원본 해시 불일치, 잘못된 이미지, 잘못된 출력은 예외로 전달한다. 후속 Worker에서 실패 안내를 연결한다.

## 실행

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`.

```powershell
.\venv311\Scripts\python.exe -B -m client_code.appearance "대표_crop_절대경로.jpg" --device cpu
.\venv311\Scripts\python.exe -B -m unittest client_code.tests.test_appearance -v
```

현재 환경처럼 기존 venv 실행기가 원래 Python을 찾지 못할 경우, 위와 같은 디렉터리에서 가용 Python에 기존 패키지 경로를 연결한다. 설치는 필요하지 않다.

```powershell
$env:PYTHONPATH = 'C:\GitHub_Clone\AI_CCTV\venv311\Lib\site-packages'
& 'C:\Users\jdh45\.local\bin\python3.11.exe' -B -m client_code.appearance "대표_crop_절대경로.jpg" --device cpu
& 'C:\Users\jdh45\.local\bin\python3.11.exe' -B -m unittest client_code.tests.test_appearance -v
```

코드에서 재사용할 때는 인스턴스를 한 번 만들고 `analyze()`를 호출한다. PyQt의 `client_code` 실행 문맥에서는 `from appearance.analyzer import AppearanceAnalyzer`로도 사용할 수 있다.

```python
from client_code.appearance.analyzer import AppearanceAnalyzer

analyzer = AppearanceAnalyzer(device='auto')
result = analyzer.analyze(image_path)
```

출력은 `{"model_version": "exp3", "attributes": {...}}` 형식의 JSON 직렬화 가능한 dict다. gender/hat 등은 원래 영문 라벨을 유지하고 age만 합의한 한국어 범주로 반환한다. 점수나 원래 나이 클래스는 현재 결과에 별도 저장하지 않는다.

## 검증 범위

- 자동 테스트 7개 통과: 나이 5개 클래스×성별, 원래 나이 argmax 후 변환, 다중 라벨 임계값/최고 점수/빈 소지품, 출력 크기/NaN/Inf 거부, 가중치 부재/변조 거부, 실제 CPU 모델 추론·RGB 변환·이미지 부재/손상.
- 기존 `output/camera_1/2026-11-19/person_1_093101144/crop_01.jpg`를 CPU CLI와 CUDA에서 각각 분석했다. CLI JSON 출력과 CUDA 8개 속성 출력을 확인했다. 출력값은 정답 판정이나 정확도 측정 근거가 아니다.
- 가중치 원본/복사본 해시 일치 확인. 프로그램 실행 위치와 다른 작업 디렉터리의 기본 모델 경로 및 PyQt식 import는 별도 확인했다.
- 위 내용은 1단계 단독 검증 기록이다. 3단계에서는 신규 파이프라인 11개 포함 관련 80개 자동 검증과 실제 단일 웹캠/YOLO/OSNet/CNN/임시 HTTP/DB/정상 종료까지 확인했다. 상세 증거·초기 회귀 실패 기록은 개발 계획 3단계를 따른다. 예측 정확도, 지연·GPU 메모리·장시간 부하는 검증하지 않았다.

## 관제 프로그램 자동 분석

- 모든 카메라가 로그인 세션별 CNNWorker 하나/모델 하나를 공유한다. 첫 등장 이벤트 저장 성공과 원본 crop 준비 후 모델을 작업 스레드에서 로딩한다.
- CNN 큐는 32개, Coordinator의 분석/결과 저장 대기는 최대 64건이다. 초과/모델 실패/입력 실패/HTTP 저장 실패는 안내하며 기존 등장 이벤트를 삭제하지 않는다. 무한 재시도·영속 큐는 없다.
- 같은 등장 event_id당 한 번 분석한다. 동일 글로벌 ID의 다른 등장 이벤트는 따로 분석한다. 대표 crop과 같은 output 원본을 읽으므로 이벤트 폴더 rename과 무관하다. 원본을 이동/수정하지 않는다.
- CNN 완료 결과를 기존 EventStore의 인증 PATCH로 저장하며 타임라인에 새 카드를 추가하지 않는다.
- 영상/ReID/미디어 정리 후 기존 8초 종료 예산 안에서 마지막 등장 응답·CNN·결과 PATCH를 마무리한다. 초과하면 미완료 저장을 안내하고 대기 작업/늦은 결과를 버린다. 이미 실행 중인 PyTorch 추론은 강제 종료하지 않으므로 반환/스레드 종료까지 화면 전환을 기다릴 수 있다. 다음 로그인에서 과거 작업을 재전송하지 않는다.
