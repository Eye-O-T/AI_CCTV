클라이언트측 사용 라이브러리.
- ultralytics
- opencv-python
- torch
- numpy

이거 설치하면 되는데 torch는 본인 pc gpu에 맞게 설치하시면 될거에요.
아래 라이브러리는 전부 venv가상환경에서 설치해야합니다.
- 저는 파이썬 3.11로 했는데 3.12까지는 잘 돌아갈거에요. 3.13은 안 될 확률 높습니다.

## 클라이언트
1. pip install ultralytics opencv-python numpy
2. pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 
- 2번은 cuda 12.1버전입니다.
- gpu없으면 pip install torch torchvision torchaudio

## 서버
1. sudo apt update
2. sudo apt install -y python3-gi gir1.2-gst-rtsp-server-1.0 gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly

