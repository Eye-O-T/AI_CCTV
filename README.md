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

### gitignore처리 해주세요.
코드 올리실때 따로 가상환경 만들어서 실행하신분들은 가상환경 .gitignore에 추가해서 추적 안되게 해주세요.

### 가상환경 셋팅
venv가상환경 설정 방법(윈도우 기준)
```bash
py -3.11 -m venv venv311 # 가상환경 생성(버전은 가급적 파이썬 3.11버전)
venv311\Scripts\activate # 가상환경 활성화
```

라이브러리 파일 다운로드
```bash
pip install -r requirements.txt
```

torch는 각자 pc마다 맞는 버전으로 각자 설치해주셔야합니다. 
GPU 사용, CUDA 12.1 예시)
`pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121`

GPU 없거나 CPU만 사용할 경우) - 이건 비추합니다. 차라리 테스트를 위해서는 gpu부족하면 코랩 쓰는거 권장합니다.
`pip install torch torchvision torchaudio`

