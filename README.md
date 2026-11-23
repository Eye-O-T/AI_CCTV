클라이언트 실행·인증 화면·백엔드 자동 시작 및 종료를 확인하거나 수정할 때는 [클라이언트 README](client_code/README.md)를 먼저 참고하세요.

## 5/14일 추가)
### 브랜치 보호
브랜치 규칙 추가했습니다.
main브랜치 규칙
- 강제 push불가
- 무조건 PR로 본인 제외 1명이 merge눌러줘야합니다.

develop브랜치 규칙
- 강제 push불가
- 무조건 PR로 본인 포함 1명이 merge눌러야합니다.

---
# 셋팅방법 지피티로 정리한거입니다.
아래는 **새 Windows 10/11 64비트 PC에서 현재 PC 관제 프로그램을 설치하는 순서**입니다. 웹캠·라즈베리파이 RTSP·YOLO·OSNet·인상착의 CNN·장애 복구까지 사용하는 기준입니다.

명령은 모두 **PowerShell**에서 실행합니다. 현재 코드와 기존 검증 환경을 기준으로 정리했으며, 빈 PC에서 아래 전체 절차를 새로 검증한 것은 아닙니다.

**1. Git·Python·FFmpeg 설치**

|프로그램|다운로드|설치 방법|
|---|---|---|
|Git|[Git 공식 다운로드](https://git-scm.com/downloads/)|Windows용 설치. 기본 옵션 사용 가능|
|Python|[Python 3.11.9](https://www.python.org/downloads/release/python-3119/)|`Windows installer (64-bit)` 선택. 설치 시 **Add python.exe to PATH**, Python Launcher 설치 선택|
|FFmpeg|[FFmpeg Windows 빌드](https://www.gyan.dev/ffmpeg/builds/)|`ffmpeg-release-essentials.zip` 다운로드 후 압축 해제|

FFmpeg는 다음 위치에 실행 파일이 오도록 배치합니다.

```
C:\Tools\ffmpeg\bin\ffmpeg.exe
```

Windows에서 **환경 변수 편집 → 사용자 변수 → Path → 편집 → 새로 만들기**로 다음 폴더를 추가합니다.

```
C:\Tools\ffmpeg\bin
```

설치가 끝나면 **PowerShell을 새로 열고**, 아무 디렉터리에서 다음을 확인합니다.

```
git --version
py -3.11 --version
ffmpeg -version
```

FFmpeg는 네트워크 장애 후 받은 TS 파일을 복구 MP4로 만드는 데 사용됩니다. Python 패키지가 아닌 **실행 파일 설치가 필요**합니다.

**2. 프로젝트 clone**

실행 디렉터리: 처음에는 아무 위치에서 실행해도 됩니다.

```
New-Item -ItemType Directory -Force C:\GitHub_Clone
Set-Location C:\GitHub_Clone

git clone --branch dev https://github.com/jdh4568/AI_CCTV.git

Set-Location C:\GitHub_Clone\AI_CCTV
git branch --show-current
```

마지막 출력이 `dev`인지 확인합니다.

현재 PC의 `origin` 저장소를 기준으로 한 주소입니다. 비공개 저장소라면 접근 권한이 있는 GitHub 계정으로 인증해야 합니다.

**3. 새 PC에서 가상환경 생성**

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`

```
py -3.11 -m venv venv311

.\venv311\Scripts\python.exe -m pip install --upgrade pip
```

아래에서는 가상환경의 Python 경로를 직접 사용하므로, `Activate.ps1` 실행이나 PowerShell 실행 정책 변경은 필요 없습니다.

기존 PC의 `venv311` 폴더를 복사하지 말고 새로 생성하세요.

**4. PyTorch 설치**

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`

아래 두 가지 중 **한 가지만** 선택합니다.

- **CPU로 실행할 경우**

```
.\venv311\Scripts\python.exe -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
```

- **기존 검증 환경과 같은 CUDA 12.1 조합을 지원하는 NVIDIA GPU를 사용할 경우**

먼저 해당 GPU의 [NVIDIA 드라이버](https://www.nvidia.com/Download/index.aspx)를 설치한 뒤 실행합니다.

```
.\venv311\Scripts\python.exe -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
```

이 조합은 기존 OSNet·CNN 검증에 사용한 버전이며, 명령은 [PyTorch 공식 이전 버전 안내](https://pytorch.org/get-started/previous-versions/)를 따릅니다. **새로운 GPU는 이 구버전 조합에서 지원되지 않을 수 있으므로**, 그런 경우 GPU에 맞는 PyTorch 조합을 선택해야 합니다.

**5. 관제 프로그램에 필요한 패키지 설치**

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`

```
.\venv311\Scripts\python.exe -m pip install `
  "ultralytics==8.4.77" `
  "opencv-python==4.13.0.92" `
  "lap==0.5.13" `
  "PyQt5==5.15.11" `
  Pillow requests psutil `
  fastapi uvicorn `
  "pydantic>=2,<3" `
  "SQLAlchemy>=2.0,<2.1" `
  "PyJWT>=2.10,<3" `
  "argon2-cffi>=25.1,<26"
```

NumPy·SciPy·PyYAML 등 하위 의존성은 함께 설치됩니다.

현재 루트 `requirements.txt`에는 과거 Qwen·얼굴인식 실험용 패키지가 섞여 있으므로, **이번 설치에서는 위 명령을 사용합니다.** 현재 관제 흐름에는 Qwen·InsightFace·전체 `torchreid` 패키지를 별도로 설치할 필요가 없습니다.

설치 후 확인합니다.

```
.\venv311\Scripts\python.exe -m pip check

.\venv311\Scripts\python.exe -c "import torch, torchvision, cv2, ultralytics, lap, PyQt5, fastapi, sqlalchemy; print('패키지 import 성공'); print('torch:', torch.__version__); print('CUDA 사용 가능:', torch.cuda.is_available())"
```

CPU 설치에서는 CUDA 결과가 `False`여도 정상입니다.

**6. 모델 가중치 3개 준비**

최종적으로 다음 파일들이 있어야 합니다.

```
C:\GitHub_Clone\AI_CCTV\
├─ venv311\
└─ client_code\
   ├─ main.py
   ├─ yolo26s.pt
   ├─ reid\
   │  └─ weights\
   │     └─ osnet_x1_0_msmt17_combineall.pth
   └─ appearance\
      └─ weights\
         └─ par_serving_exp3.pt
```

이 가중치들은 Git에서 제외되어 있으므로 별도로 준비합니다.

**① YOLO**

기존 PC의 다음 파일을 새 PC의 같은 위치에 복사합니다.

```
C:\GitHub_Clone\AI_CCTV\client_code\yolo26s.pt
```

기존 파일을 가져올 수 없다면, 인터넷에 연결된 상태에서 공식 사전학습 가중치를 받을 수 있습니다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV\client_code`

```
Set-Location C:\GitHub_Clone\AI_CCTV\client_code

..\venv311\Scripts\python.exe -c "from ultralytics import YOLO; YOLO('yolo26s.pt')"
```

Ultralytics는 모델 이름을 지정해 사전학습 가중치를 로딩하는 방식을 지원합니다. [YOLO26 공식 안내](https://docs.ultralytics.com/models/yolo26/)

**② OSNet**

프로젝트의 다운로드 도구를 사용합니다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`

```
Set-Location C:\GitHub_Clone\AI_CCTV

.\venv311\Scripts\python.exe -m client_code.reid.download_weights
```

다음 위치에 다운로드되며, 도구가 파일 크기와 해시를 확인합니다.

```
client_code\reid\weights\osnet_x1_0_msmt17_combineall.pth
```

**③ 인상착의 CNN**

이 모델은 **사용자가 제공한 자체 학습 파일**이므로 기존 PC에서 가져와야 합니다.

```
파일 이름: par_serving_exp3.pt

새 PC 배치 위치:
C:\GitHub_Clone\AI_CCTV\client_code\appearance\weights\par_serving_exp3.pt
```

`weights` 폴더가 없다면 생성합니다. 현재 구현은 파일 해시를 확인하므로 이름만 같은 다른 모델로 대체하면 안 됩니다.

**7. 프로그램 실행**

현재 YOLO 경로는 상대 경로이므로 **반드시 `client_code` 폴더에서 실행**하는 방식으로 맞춥니다.

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV\client_code`

```
Set-Location C:\GitHub_Clone\AI_CCTV\client_code

..\venv311\Scripts\python.exe main.py
```

이 명령은 나중에 프로그램을 실행할 때도 동일하게 사용합니다.

프로그램이 로컬 FastAPI 백엔드를 자동 실행하므로 별도 서버 실행 명령은 필요 없습니다. 현재 로컬 API는 `127.0.0.1:9000`을 사용하며, DB는 SQLite여서 별도 DB 서버 설치도 필요 없습니다.

**8. 최초 실행 후 화면에서 설정**

1. **관리자 등록 → 로그인**
    
    - 새 DB에서는 관리자 등록 화면이 나옵니다.
    - 기존 PC의 계정·카메라·이벤트 정보는 clone만으로 이전되지 않습니다.
2. **설정 → 저장 설정 → 위치 선택 → 저장**
    
    - 예: `C:\CCTV_Data`를 선택하면 아래에 `AI_CCTV` 폴더가 생성됩니다.
    - 현재 저장 방식에 맞춰 **프로젝트와 같은 드라이브인 C:의 별도 폴더**를 사용하세요.
    - 첫 이벤트 저장 뒤에는 저장 경로 변경이 제한되므로 처음에 정합니다.
    - 서버 저장 설정이 성공했다는 안내를 확인합니다.
3. **설정 → 분석 설정**
    
    - `YOLO 사람 탐지/추적 사용`을 켭니다.
    - 현재 인상착의 CNN은 등장 이벤트 이후 자동 분석합니다.
    - 화면의 비활성화된 VLM 옵션은 켤 필요 없습니다.
4. **카메라 등록 / 관리**
    
    - 웹캠: 종류 `webcam`, 장치 번호는 보통 `0`부터 확인합니다.
    - 라즈베리파이: 종류 `edge_node`, 실제 RTSP 주소를 입력합니다.
    - 장애 복구를 사용하려면 **엣지노드 API 기본 주소**도 입력해야 합니다. `/recover`는 코드가 붙이므로 기본 주소까지만 입력합니다.
    - RTSP 주소와 API 주소는 기존 라즈베리파이의 실제 설정을 사용합니다.
5. **카메라 START**
    
    - 영상 수신·사람 탐지·글로벌 ID 표시를 확인합니다.
    - 정상 STOP 후 원본 녹화와 이벤트 파일이 생성되는지 확인합니다.

라즈베리파이 사용 시에는 **라즈베리파이의 송출·복구 서버가 실행 중이고 새 PC에서 해당 주소에 접근 가능해야 합니다.** 위 절차는 PC 관제 프로그램 설치 범위입니다.