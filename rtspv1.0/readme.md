# 1. 패키지 목록 업데이트
sudo apt update

# 2. GStreamer 핵심 라이브러리 및 카메라 유틸리티 설치
sudo apt install -y gstreamer1.0-tools gstreamer1.0-plugins-base \
                    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
                    gstreamer1.0-plugins-ugly gstreamer1.0-libav \
                    gstreamer1.0-rtsp libcamera-v4l2 python3-pip wget tar

# 3. RTSP 송출/백업/API 통합 실행
python3 run_rtsp_server.py

# 로그 파일
# - logs/api.log
# - logs/stream_and_record.log
# - logs/mediamtx.log
# - logs/runner.log
