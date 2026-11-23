# 라즈베리파이 RTSP 송출·백업 실행

실행 디렉터리: 라즈베리파이의 `~/AI_CCTV/rtspv1.0` (실제 복사 위치가 다르면 해당 폴더).

```bash
cd ~/AI_CCTV/rtspv1.0

# 1. 패키지 목록 업데이트
sudo apt update

# 2. GStreamer 핵심 라이브러리 및 카메라 유틸리티 설치
sudo apt install -y gstreamer1.0-tools gstreamer1.0-plugins-base \
                    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
                    gstreamer1.0-plugins-ugly gstreamer1.0-libav \
                    gstreamer1.0-rtsp libcamera-v4l2 python3-pip wget tar v4l-utils

# 파이 카메라를 사용할 때 libcamerasrc가 없다면 OS에 맞는 libcamera GStreamer
# 패키지(보통 gstreamer1.0-libcamera)도 필요하다.
# 파이 카메라 목록 조회용 rpicam-hello 또는 구버전 libcamera-hello가 필요하다.

# 3. RTSP 송출/백업/API 통합 실행
python3 run_rtsp_server.py
```

로그: `logs/api.log`, `logs/stream_and_record.log`, `logs/mediamtx.log`, `logs/runner.log`.

## 카메라 자동 선택 MVP — 2단계 (2026-11-21)

`stream_and_record.sh`에서 **프로그램 시작 시 한 번** 입력을 자동 선택한다.

| 연결 상태 | 선택/처리 |
| --- | --- |
| USB UVC 웹캠 연결 | USB 우선 선택 |
| USB 없음, 파이 카메라 있음 | 기존 libcamerasrc 입력 선택 |
| 둘 다 없음 | CAMERA ERROR 출력 후 종료 |
| 선택한 장치 조회/입력 실패, 지원 규격 없음 | 오류 출력 후 종료. 다른 장치로 자동 우회하지 않음 |

- USB는 uvcvideo 장치의 실제 영상 포맷 조회가 가능한 노드를 찾는다. 내부 코덱 노드와 USB 메타데이터 노드는 선택하지 않는다. 여러 USB 웹캠의 사용자 선택은 지원하지 않으며 처음 발견한 영상 노드를 사용한다. `/dev/video0`으로 고정하지 않는다.
- USB 후보 순서는 1280×720 30FPS → 640×480 30FPS → 640×360 30FPS → 640×480 15FPS → 640×360 15FPS다. 각 규격에서 MJPEG(MJPG)를 우선하고 YUYV도 지원한다. v4l2-ctl이 discrete 해상도/FPS로 보고한 조합만 선택하며 H.264/기타 포맷과 stepwise 규격은 이번 MVP 범위 밖이다.
- MJPEG는 jpegdec로 디코딩하고 YUYV는 GStreamer의 YUY2로 연결한다. 이후 기존 videoconvert·x264enc·백업 녹화·MediaMTX 송출 경로를 공유한다. 파이 카메라는 기존 1920×1080·30FPS 설정을 유지한다.
- 선택 종류·USB 장치 경로·포맷·해상도·FPS를 `[CAMERA]` 로그로 표시한다. 중계기 시작 전 짧은 프레임 수신 검사를 하며 약 12초 제한(강제 종료 대기 최대 2초 추가) 안에 실패하면 종료한다. 이 검사는 전체 송출/인코딩 성능 검증은 아니다.
- 실행 중 카메라 분리/교체 자동 전환은 지원하지 않는다. 장치를 바꿨으면 송출 프로그램을 정상 종료하고 다시 실행한다. PC와의 랜선 단절 후 RTSP 수신 재연결은 별개의 기존 기능이다.
- PC 관제 코드·백엔드·장치 API·실행 관리 코드는 수정하지 않았다. RTSP는 기존 `rtsp://라즈베리파이IP:8554/live`, 장치 API는 기존 8002 포트를 사용한다.

### 1단계 장치 확인 결과 (사용자 제공 출력)

- OS: Debian 12 Bookworm. USB: 삼성 Android Webcam, uvcvideo.
- `/dev/video0`: Video Capture, MJPG, 640×360/640×480/1280×720/1920×1080 및 각 15/24/30/60FPS를 보고했다. `/dev/video1`에서는 영상 형식이 열거되지 않았다.
- v4l2src, jpegdec, videoconvert, x264enc, libcamerasrc 설치를 확인했다. 실제 프레임 수신·인코딩/송출·파이 카메라 인식은 이 출력으로 확인한 것이 아니다.

### 2단계 로컬 검증과 남은 확인

- Git Bash `bash -n`, `git diff --check` 통과.
- 장치/외부 명령을 대체한 로컬 분기 검증 8개 통과: MJPEG 720p30 선택, 둘 다 연결 시 USB 우선, YUYV VGA30 선택, USB 메타데이터 노드 제외, USB 없는 파이 카메라 선택, 카메라 없음, 미지원 규격, 입력 실패, jpegdec 누락. 메타데이터 제외는 USB 선택 시나리오 안에서 함께 확인했다.
- 위 검증은 선택 분기/명령 구성 검증이며 실제 Linux 장치나 GStreamer로 수행한 검증이 아니다.

3단계에서는 변경한 스크립트를 라즈베리파이에 복사한 뒤 위 실행 디렉터리에서 실행한다. 기존 송출이 켜져 있다면 먼저 정상 종료한다.

1. USB만 연결: `[CAMERA] USB ... MJPG 1280x720 30fps` 확인 → PC 영상 표시와 백업 파일 재생 확인.
2. 파이 카메라만 연결: `[CAMERA] Pi camera ...` 확인 → PC 영상 표시와 백업 파일 재생 확인. 파이 카메라 케이블 변경은 전원을 끈 상태에서 수행한다.
3. 둘 다 연결: USB 우선 선택 확인. 둘 다 없음: 명확한 오류로 종료하는지 확인.
4. 선택한 카메라로 랜선 분리·재연결 후 PC 영상 재개, Ctrl+C 정상 종료와 재실행 확인.

두 물리 카메라 경로의 송출·녹화, 장시간 성능, 랜선 분리·재연결은 아직 미검증이다. 삼성 Android Webcam 외의 USB 웹캠 지원도 실제 장치별로 확인해야 한다.

입력 파이프라인 참고: [GStreamer v4l2src](https://gstreamer.freedesktop.org/documentation/video4linux2/v4l2src.html), [Raspberry Pi 카메라 도구](https://www.raspberrypi.com/documentation/accessories/camera-software.html).
