# RTSP Tee 분기 기반 실시간 스트리밍 및 로컬 백업 시스템 DSD 가이드

---

## 목차

1. [시스템 전체 구조 및 데이터 흐름](https://www.notion.so/dsd-366dd18ce55b80bc8ab9e2632d5b5ce1?pvs=21)
2. [알아야 하는 핵심 이론 (바이브 코딩 탈출하기)](https://www.notion.so/dsd-366dd18ce55b80bc8ab9e2632d5b5ce1?pvs=21)
3. [GStreamer 송출 스크립트 (`stream_and_record.sh`) 라인 바이 라인 분석](https://www.notion.so/dsd-366dd18ce55b80bc8ab9e2632d5b5ce1?pvs=21)
4. [PC 수신 파이썬 코드 (`rtsp_receiver.py`) 라인 바이 라인 분석](https://www.notion.so/dsd-366dd18ce55b80bc8ab9e2632d5b5ce1?pvs=21)
5. [DSD 담당자 전달용 DSD 텍스트 및 표 (복사 가능)](https://www.notion.so/dsd-366dd18ce55b80bc8ab9e2632d5b5ce1?pvs=21)

---

## 1. 시스템 전체 구조 및 데이터 흐름

전체 시스템은 라즈베리파이(Edge Node)에서 영상을 촬영하여 **동시에 두 갈래(Tee)로 처리**합니다. 한 갈래는 로컬 SD카드에 10초 단위 파일로 연속 백업되고, 다른 한 갈래는 내부 네트워크를 통해 PC(AI Server / Client)로 전송됩니다.

### 1.1 시스템 블록 다이어그램 (Mermaid)


graph TD
    subgraph PiEdge ["Raspberry Pi 4 (Edge Node)"]
        Camera[Pi Camera Module 3] -->|Raw Video| libcamerasrc[libcamerasrc]
        libcamerasrc -->|Raw YUV/RGB| videoconvert[videoconvert]
        videoconvert -->|Convert| x264enc[x264enc H.264 Compression]
        x264enc -->|H.264 Stream| h264parse[h264parse]
        h264parse -->|SPS/PPS Headers| Tee[tee: Stream Splitter]

        %% 분기 1: 로컬 백업
        Tee -->|Branch 1| Queue1[Queue 1: Large Buffer]
        Queue1 -->|H.264| splitmuxsink[splitmuxsink]
        splitmuxsink -->|10s TS Segments| SDCard[("Local SD Card: ./backups")]

        %% 분기 2: 실시간 송출
        Tee -->|Branch 2| Queue2[Queue 2: Leaky Buffer]
        Queue2 -->|FLV Format| flvmux[flvmux]
        flvmux -->|RTMP Stream| rtmpsink[rtmpsink]
        rtmpsink -->|rtmp://127.0.0.1:1935/live| MediaMTX[MediaMTX RTSP Server]
    end

    subgraph PCClient ["PC Client (AI Server / Notebook)"]
        MediaMTX -->|RTSP Protocol: Port 8554| Network(("Ethernet LAN / Wi-Fi"))
        Network -->|rtsp://192.168.99.200:8554/live| SocketKnock[TCP Port Knocker]
        SocketKnock -->|Success| OpenCV[OpenCV cv2.VideoCapture]
        OpenCV -->|Decoded Frames| MainLoop[Main Logic / UI Dashboard]

        Watchdog[Watchdog Thread] -.->|Monitor 5s Freeze| OpenCV
    end

### 1.2 핵심 데이터 경로 설명

1. **영상 취득**: 라즈베리파이의 카메라 센서가 `libcamerasrc` 엘리먼트를 통해 영상 프레임을 초당 30프레임(30 FPS), 해상도 640x480으로 받아옵니다.
2. **압축(인코딩)**: 하드웨어 성능 한계를 고려하여 CPU 4스레드를 병렬로 사용하는 `x264enc` 소프트웨어 코덱을 사용해 고효율 H.264 비디오 스트림으로 압축합니다.
3. **분기(Tee)**: GStreamer의 `tee` 플러그인을 사용하여 동일한 압축 데이터 스트림을 메모리 상에서 두 개로 분할합니다.
4. **로컬 백업 (Tee 1)**: 안전 버퍼(`queue`)를 거쳐 10초 단위(`max-size-time=10s`)마다 `.ts` (MPEG-TS) 포맷 파일로 나누어 로컬 디렉토리에 저장합니다.
5. **실시간 송출 (Tee 2)**: 프레임 누수형 버퍼(`leaky queue`)를 거쳐 가벼운 스트리밍 규격인 `flvmux`로 패킹된 뒤, 라즈베리파이 내부에 상주 중인 초경량 미디어 서버인 `MediaMTX`로 전달(`rtmpsink`)됩니다.
6. **PC 수신**: 노트북 PC는 라즈베리파이의 `MediaMTX`가 열어둔 RTSP 포트(`8554`)로 접속하여 실시간으로 디코딩된 영상을 수신 및 화면에 렌더링합니다.

---

## 2. 알아야 하는 핵심 이론 (바이브 코딩 탈출하기)

코드가 왜 이렇게 짜여 있는지 동작 메커니즘을 이론적으로 이해해야 면접이나 발표 시 정확히 설명할 수 있습니다.

### 2.1 RTSP와 RTMP의 차이점 및 MediaMTX 우회 설계 이유

- **RTSP (Real Time Streaming Protocol)**: 비디오/오디오 멀티미디어 스트림을 제어하기 위한 프로토콜입니다. 저지연성(Low-latency)이 우수하여 CCTV 및 실시간 모니터링 시스템의 업계 표준으로 쓰입니다.
- **RTMP (Real Time Messaging Protocol)**: 과거 어도비 플래시 시절 개발된 스트리밍 프로토콜로, 주로 유튜브/트위치 같은 방송 플랫폼으로 영상을 송출할 때 널리 쓰입니다. TCP 기반이라 안정적입니다.
- **왜 RTMP로 송출해서 RTSP로 바꾸나요?**
    - GStreamer에 내장된 RTSP 송출 모듈인 `rtspclientsink`는 라즈베리파이의 특정 라이브러리와 메모리 매핑 구조에서 **만성적인 포인터 해제 오류(버그)를 발생시켜 수시로 크래시**가 납니다.
    - 이를 우회하기 위해, 라즈베리파이 내부에서만 돌아가는 초경량 중계 서버인 **MediaMTX**를 띄우고, 라즈베리파이 내부 루프백 IP(`127.0.0.1:1935`)로 아주 안정적인 **RTMP 스트림**을 쏩니다.
    - MediaMTX는 이를 받아 PC 노트북이 원격으로 쉽게 접속할 수 있도록 표준 **RTSP 주소(`rtsp://라즈베리파이IP:8554/live`)로 즉시 재포장(Transmuxing)**해 줍니다.
    - 덕분에 에러율이 거의 0%에 수렴하는 단단한 통신망이 완성됩니다.

### 2.2 MPEG-TS 포맷과 splitmuxsink를 쓰는 이유

- **MP4 포맷의 한계**: MP4 파일은 재생에 필요한 핵심 메타데이터(헤더 정보인 `moov atom`)가 **녹화가 정상적으로 완전히 끝나서 파일을 닫는 시점**에 파일의 맨 뒤에 작성됩니다. 따라서 정전이나 강제 종료 등으로 인해 녹화가 중단되면 메타데이터가 유실되어 파일 전체가 깨져 재생할 수 없습니다.
- **MPEG-TS (Transport Stream) 포맷의 장점**: TS 포맷은 영상을 독립적인 작은 단위의 패킷으로 나누어 저장하기 때문에, **녹화가 중간에 뚝 끊기더라도 끊기기 직전 프레임까지 완벽하게 보존 및 재생**됩니다. 상시 전원 차단 위험이 있는 라즈베리파이 기반 CCTV에 가장 적절한 포맷입니다.
- **splitmuxsink**: 이 엘리먼트는 GStreamer 내부에서 설정한 시간(여기선 10초) 단위로 영상을 알아서 끊어서 파일로 쪼개어 저장해주며, 저장하는 동안 스트림이 멈추지 않도록 독립적으로 기능합니다.

### 2.3 비동기 파일 저장 (async-handling=true)과 Leaky Queue의 관계

- **문제 상황**: SD카드(Class 10 카드를 써도 마찬가지)는 순간적인 쓰기 연산 시 심각한 I/O 병목(지연)이 발생합니다. GStreamer 파이프라인에서 한쪽 갈래(저장부)가 SD카드를 쓰느라 버벅대면, 파이프라인 전체 싱크가 굳어버려 다른 쪽 갈래(실시간 송출부)도 프레임이 멈추는 동반 지연이 일어납니다.
- **해결법**:
    1. `splitmuxsink`에 `async-handling=true` 옵션을 부여하여, 파일 분할 및 쓰기 연산을 **메인 스트리밍 스레드와 완전히 분리된 독립된 가상 스레드**에서 비동기로 수행하도록 격리합니다.
    2. 실시간 송출 큐에 `leaky=downstream` 속성을 적용합니다. 네트워크가 일시적으로 버벅거리거나 디코딩이 밀리면, 버퍼(큐)에 새로 들어오는 프레임을 기다려주지 않고 **버퍼에 쌓여있던 가장 오래된 프레임을 즉시 폐기(Leaky)**합니다. 이를 통해 송출 프레임의 대기 시간을 실시간 수준으로 계속 유지합니다.

### 2.4 TCP Port Knocking을 이용한 OpenCV 프리징 방지

- **OpenCV의 치명적 한계**: OpenCV의 `cv2.VideoCapture("rtsp://...")` 함수는 RTSP 서버의 전원이 꺼져있거나 네트워크가 물리적으로 단선되었을 때, 내부 FFmpeg 라이브러리 엔진이 타임아웃을 판정할 때까지 **최대 30초 동안 메인 실행 스레드 전체를 굳어버리게 만듭니다(Blocking/Hang)**. 이 기간 동안 GUI 창도 반응이 없어지고 프로그램이 먹통이 됩니다.
- **사전 노크(Port Knocking) 기법**: RTSP 연결을 걸기 전, 가볍고 빠른 TCP 소켓 통신을 이용해 라즈베리파이의 RTSP 포트(`8554`)로 연결을 시도해 봅니다. 타임아웃을 `1.5초`로 아주 짧게 잡고 미리 찔러봐서, 포트가 닫혀있다면 `cv2.VideoCapture` 자체를 호출하지 않고 3초 대기 루프로 빠집니다. 이로써 30초 대기 프리징을 완전히 차단하고, 6~10초 내외의 빠른 재연결 속도를 실현합니다.

### 2.5 Multi-Threading & Watchdog (스레드 동기화와 감시견)

- **스레드 분리**: `rtsp_receiver.py`는 총 3개의 스레드가 동시에 실행됩니다.
    1. **메인 스레드**: OpenCV 화면창을 띄우고 프레임을 화면에 렌더링하는 역할.
    2. **수신 스레드 (`_receive_loop`)**: 백그라운드에서 끊임없이 RTSP 영상 데이터를 받아 메모리 버퍼(`self.frame`)에 최신 본을 갱신하는 역할.
    3. **감시 스레드 (`_watchdog_loop`)**: 1초마다 깨어나 마지막으로 수신된 프레임 시각을 감시하는 역할.
- **Watchdog(감시견) 필요성**: TCP 포트가 열려서 접속에 성공했더라도, 무선 네트워크 신호가 약해져서 패킷 송수신이 뚝 끊기면 OpenCV 내부 리포트 루프가 행(Hang)에 걸려 무한 대기 상태가 됩니다. 이때 감시 스레드가 5초 동안 갱신된 프레임이 없다면 네트워크 장애로 판단, 락(Lock)을 쥐고 `self.cap.release()`를 강제로 실행하여 기존의 맛이 간 연결 소켓을 물리적으로 부숴버립니다. 소켓이 부서지면 수신 스레드는 에러를 인식하고 즉시 빠져나와 재연결 루틴(Port Knocking부터 다시 시작)을 재가동합니다.

---

## 3. GStreamer 송출 스크립트 (`stream_and_record.sh`) 라인 바이 라인 분석

라즈베리파이에서 동작하는 GStreamer 파이프라인의 전체 실행 명령어 분석입니다.

```bash
gst-launch-1.0 -e \\
```

- **`gst-launch-1.0`**: GStreamer 파이프라인을 커맨드라인에서 즉석으로 빌드하고 구동하는 실행 도구입니다.
- **`e` (EOS 전파)**: 사용자가 스트리밍을 종료(`Ctrl + C`)했을 때, 파일 기록 모듈인 `splitmuxsink`에게 "이제 파일 작성을 종료해라"라는 마무리 신호(End-Of-Stream)를 정상적으로 보냅니다. 이 옵션이 있어야 마지막 녹화 파일의 헤더 정보가 깔끔하게 마감 저장됩니다.

```bash
    libcamerasrc ! \\
```

- **`libcamerasrc`**: 라즈베리파이 공식 카메라 라이브러리(libcamera)로부터 직접 카메라 센서 데이터(Raw YUV 또는 RGB)를 가져오는 입력 장치 소스 엘리먼트입니다.

```bash
    video/x-raw,width=${RESOLUTION_WIDTH},height=${RESOLUTION_HEIGHT},framerate=${FPS}/1 ! \\
```

- **`video/x-raw...`**: 카메라 소스에게 전달할 프레임의 규격(Capabilities, Caps)을 명시합니다. 640x480 해상도와 초당 30프레임으로 하드웨어 수준에서 강제 튜닝하여 비디오 크기를 최적화합니다.

```bash
    videoconvert ! \\
```

- **`videoconvert`**: 카메라 원시 색상 정보 포맷(예: YUY2)을 뒤이어 올 H.264 인코더 코덱이 처리 가능한 색상 정보 포맷(I420 등)으로 변환해주는 컬러 스페이스 컴포넌트입니다.

```bash
    x264enc tune=zerolatency speed-preset=ultrafast bitrate=500 key-int-max=15 bframes=0 threads=4 sliced-threads=true ! \\
```

- **`x264enc`**: 소프트웨어 H.264 인코딩을 수행하는 모듈입니다.
    - `tune=zerolatency`: 스트림 인코딩 중 프레임 버퍼링을 제거하여 화면 입력 대비 지연 시간(Latency)을 0에 가깝게 만듭니다.
    - `speed-preset=ultrafast`: CPU 연산 시간을 최소화하여 라즈베리파이의 발열과 CPU 점유율을 대폭 낮춥니다.
    - `bitrate=500`: 영상 스트림 전송 속도를 500kbps로 제어합니다. 라즈베리파이 환경에서 무선 랜 대역폭 초과로 소켓 버퍼가 폭발하거나 지연되는 것을 원천 방지합니다.
    - `key-int-max=15`: 15프레임(30fps 기준 0.5초)마다 강제로 비디오 복원에 절대적인 Key Frame(I-Frame)을 삽입합니다. PC 클라이언트가 언제 재연결하더라도 0.5초 이내에 정상 화면을 뿌릴 수 있게 합니다.
    - `bframes=0`: 연산이 복잡하고 지연을 유발하는 화면 예측 B-Frame 사용을 완전 금지합니다.
    - `threads=4 sliced-threads=true`: 라즈베리파이 4의 쿼드코어 CPU 전체에 인코딩 연산 부담을 골고루 나눕니다. (이 세팅을 빼면 1번 코어만 혹사당해 무조건 영상이 버벅댑니다.)

```bash
    h264parse config-interval=1 ! \\
```

- **`h264parse`**: H.264 비디오 패킷의 헤더 정보를 규격화하는 구문 분석기입니다.
    - `config-interval=1`: 화면 복원에 필요한 디코더 구성 정보(SPS/PPS 헤더)를 매 1초마다 강제 반복 주입합니다. 실시간 접속자가 접속 시 헤더를 받지 못해 검은 화면만 보며 대기하는 시간을 예방합니다.

```bash
    tee name=t \\
```

- **`tee name=t`**: 위에서 가공된 H.264 스트림을 가상의 이름 `t`로 저장하고, 컨베이어 벨트를 두 개로 쪼개는 분기 엘리먼트입니다.

---

### [분기 1: SD카드 로컬 백업]

```bash
    t. ! queue max-size-buffers=150 max-size-time=0 max-size-bytes=0 ! \\
```

- **`t.`**: `tee`의 첫 번째 분기 출력물입니다.
- **`queue...`**: 파일 저장 스레드가 일을 하기 위해 임시로 비디오 패킷을 담아두는 여유로운 메모리 대기 줄(큐)입니다.

```bash
       splitmuxsink location="${BACKUP_DIR}/backup_${START_TIME}_%05d.ts" max-size-time=10000000000 async-handling=true \\
```

- **`splitmuxsink`**: 다중 분할 저장을 담당하는 종착점(Sink)입니다.
    - `location=..._%05d.ts`: 파일명 뒤에 5자리 일련번호(`00001.ts`, `00002.ts`...)를 자동으로 메겨 저장합니다.
    - `max-size-time=10000000000`: 나노초 단위 기준 10초(`10 * 10억 ns`)마다 새로운 파일로 자동 분할합니다.
    - `async-handling=true`: 파일 생성 시 발생하는 I/O 병목이 메인 파이프라인에 전파되지 않도록 비동기 서브 스레드로 구동합니다.

---

### [분기 2: 노트북 실시간 송출]

```bash
    t. ! queue leaky=downstream max-size-buffers=60 max-size-time=0 max-size-bytes=0 ! \\
```

- **`t.`**: `tee`의 두 번째 분기 출력물입니다.
- **`queue leaky=downstream`**: 네트워크 대역폭 부족으로 버벅임 발생 시 버퍼 안의 오래된 프레임을 즉시 털어버려(Leaky) 지연 현상을 막는 특수 누수형 큐입니다.

```bash
       flvmux streamable=true ! \\
```

- **`flvmux streamable=true`**: RTMP 스트리밍의 표준 컨테이너 구조인 FLV 규격으로 비디오 패킷을 감싸줍니다.

```bash
       queue leaky=downstream max-size-buffers=60 max-size-time=0 max-size-bytes=0 ! \\
```

- **`queue leaky=...`**: 혹여나 네트워크 송출단(`rtmpsink`)이 꽉 막혔을 경우를 대비해 2차 프레임 드랍(누수) 안전장치를 둡니다.

```bash
       rtmpsink location="rtmp://127.0.0.1:1935/${RTSP_PATH}"
```

- **`rtmpsink`**: 로컬 컴퓨터 내부의 MediaMTX 서버 RTMP 입력 포트(`1935`)로 변환된 패킷 데이터를 전송(Dump)하여 최종 마감 처리합니다.

---

## 4. PC 수신 파이썬 코드 (`rtsp_receiver.py`) 라인 바이 라인 분석

노트북에서 동작하는 OpenCV 수신 모듈 코드의 핵심 부분 상세 분석입니다.

### 4.1 FFmpeg 백엔드 옵션 주입

```python
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp;stimeout;2000000;timeout;2000000"
```

- OpenCV가 RTSP 스트림을 해석할 때 사용하는 기본 엔진인 **FFmpeg** 라이브러리에 환경 변수를 통해 커스텀 셋팅을 강제로 먹입니다.
    - `rtsp_transport;tcp`: 기본 UDP 통신 대신 데이터 유실이 없고 화면 깨짐이 덜한 **TCP 프로토콜**을 강제 사용합니다.
    - `stimeout;2000000;timeout;2000000`: RTSP 핸드셰이크 및 스트림 대기 시간을 각각 **2초(2백만 마이크로초)**로 고정하여 비정상 먹통 증상을 2초 내외로 조기 차단합니다. 옵션 구분을 콜론이나 콤마가 아닌 **세미콜론(`;`)**으로 해야 FFmpeg C++ 라이브러리가 오류 없이 해석합니다.

### 4.2 TCP Port Knocker (재연결 신속화의 핵심)

```python
def check_server_port_open(rtsp_url, timeout=1.5):
```

- RTSP 연결을 맺기 전 라즈베리파이의 네트워크 상태를 미리 노크(Knock)하는 저수준 함수입니다.

```python
        parsed = urlparse(rtsp_url)
        hostname = parsed.hostname
        port = parsed.port if parsed.port is not None else 8554
```

- `urllib.parse.urlparse`를 이용해 RTSP 주소(예: `rtsp://192.168.99.200:8554/live`)에서 호스트 이름(`192.168.99.200`)과 포트 번호(`8554`)만 정밀하게 도려냅니다.

```python
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((hostname, port))
        sock.close()
        return True
```

- 가벼운 **TCP 소켓**을 열어 지정된 타임아웃(`1.5초`) 안에 라즈베리파이에 TCP 3-way handshake가 성립되는지 체크합니다. 성립되면 포트가 열린 것이므로 `True`를, 연결에 실패하면 예외(Exception)로 떨어져 즉시 `False`를 반환합니다.
- 이 단계를 거치면, 라즈베리파이가 부팅 중이거나 꺼진 경우에 OpenCV의 `cv2.VideoCapture` 호출을 원천 차단하여 프로그램 전체 프리징을 막을 수 있습니다.

### 4.3 RTSP 수신기 클래스 (`RTSPReceiver`) 주요 로직

```python
        self.lock = threading.Lock()
```

- **Thread Lock**: 메인 스레드(화면 갱신)와 수신 스레드(비디오 캡처)가 동일한 전역 메모리 공간(`self.frame`, `self.cap`)에 동시에 접근하여 데이터를 읽고 쓰는 과정에서 메모리가 꼬이는 현상(Race Condition)을 원천 차단하는 스레드 열쇠(뮤텍스) 장치입니다. `with self.lock:` 구문을 통과한 스레드만 해당 리소스에 손을 댈 수 있습니다.

```python
    def _watchdog_loop(self):
        while self.running:
            time.sleep(1.0)
            if self.is_connected:
                if time.time() - self.last_frame_time > 5.0:
                    print("[!] Watchdog: Stream freeze detected. Forcing resource release...")
                    with self.lock:
                        if self.cap is not None:
                            self.cap.release()
```

- **Watchdog(감시견) 루프**: 1초마다 루프를 돌며, 현재 연결된 상태(`self.is_connected = True`)임에도 불구하고 최신 프레임을 받아온 마지막 시각(`self.last_frame_time`)이 **5초 전**이라면 네트워크 좀비 현상(소켓 동결)으로 규정합니다.
- 락을 걸고 OpenCV 비디오 캡처 인스턴스(`self.cap`)의 리소스를 수동으로 직접 해제(`release()`)하여 소켓을 파괴합니다. 이를 통해 무한 대기에 빠진 `_receive_loop`를 잠에서 강제적으로 깨웁니다.

```python
    def _receive_loop(self):
        was_unreachable = True
        while self.running:
            if not check_server_port_open(self.rtsp_url, timeout=1.5):
                was_unreachable = True
                time.sleep(self.reconnect_interval)
                continue
```

- 백그라운드에서 무한히 돌며 RTSP 프레임을 땡겨오는 심장 같은 루프입니다.
- 1단계로 Port Knocking을 통해 포트가 열려 있는지 감시하고, 닫혀있다면 3초 간격(`reconnect_interval`)으로 계속 노크만 하며 대기합니다.

```python
            if was_unreachable:
                print("[~] RTSP Server port is open. Waiting 1.5s for network link to stabilize...")
                time.sleep(1.5)
                was_unreachable = False
```

- **이더넷 카드 안정화 대기**: 랜선을 뺐다가 다시 꼽았을 때, 포트가 열리자마자 바로 OpenCV를 호출하면 OS의 이더넷 카드 드라이버 및 내부 라우팅 테이블이 완전히 갱신되지 않아 연결에 도중 에러가 날 확률이 높습니다. 이를 잡기 위해 물리 포트가 감지되면 일부러 `1.5초` 동안 이더넷 통로가 안정되기를 기다립니다.

```python
            cap = cv2.VideoCapture(self.rtsp_url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
```

- 소켓 수신 인스턴스를 즉각 기동하고, 프레임 버퍼 크기를 `1`로 못박아둡니다. 버퍼가 크면 최신 화면이 아닌 과거에 밀린 화면이 출력되므로 레이턴시(딜레이)를 최저치로 유지하기 위함입니다.

```python
            # 4. 동적 소켓 버퍼 플러싱 (비활성화):
            print("[i] Skipping buffer flush to prevent keyframe loss for H.264 decoder.")
```

- 예전엔 OpenCV 버퍼를 비운다고 수동으로 읽지 않은 프레임을 날려버리는 로직(`cap.grab()`)을 즐겨 썼지만, H.264 인코더 특성상 그렇게 하면 화면 전체를 구성하는 **키프레임(I-Frame)이 유실되어 디코더가 화면을 아예 복원하지 못해 재연결 시 화면이 한참 동안 나오지 않는 부작용**이 있었습니다. 따라서 버퍼 플러싱은 과감히 건너뛰고 FFmpeg의 자체 버퍼 동기화를 기다리는 형태로 튜닝했습니다.

```python
            consecutive_failures = 0
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures >= 80:
                        print("[!] Frame read failed consecutively 80 times. Connection might be lost. Reconnecting...")
                        break
                    time.sleep(0.01)
                    continue

                consecutive_failures = 0
                self.last_frame_time = time.time()
                with self.lock:
                    self.frame = frame.copy()
```

- **오차 허용 및 실패 임계치 튜닝**: 와이파이나 랜선 통신의 특성상 순간적인 노이즈나 키프레임 소실로 프레임 한두 장 읽기에 실패(`ret == False`)하는 경우가 생깁니다. 이때 즉시 재연결을 시도하면 시스템 리소스 낭비가 큽니다.
- 프레임 수신 실패 연속 카운트(`consecutive_failures`) 한도를 **80프레임(30fps 기준 약 2.6초)**으로 여유롭게 튜닝해두어, 순간적인 통신 떨림은 묵인하고 지나가되 완전히 통신선이 끊어진 수준(연속 80회 실패)일 때만 루프를 탈출하여 재연결을 재시작하게 설계했습니다.
- 프레임 획득 성공 시에는 **메모리 복제본(`frame.copy()`)**을 만들어 보관합니다. 그냥 `self.frame = frame`을 주면 메인 스레드가 가져가서 화면을 그릴 때 주소 포인터 충돌로 이미지 데이터가 깨지는 세그멘테이션 폴트(Segmentation Fault)를 일으킬 수 있습니다.

---

## 5. DSD 담당자 전달용 DSD 텍스트 및 표 (복사 가능)

DSD 작성 담당자에게 그대로 던져주면 DSD에 조립해 넣을 수 있는 정형화된 상세 사양 텍스트들입니다.

### 5.1 하드웨어 및 보드 선정 검토서 (DSD 2장/HW 부문용)

### [요구사항 정의 및 검토]

1. **하드웨어 H.264 인코딩 지원**: CPU 부담 최소화 및 발열 제어로 24시간 안정 작동성 확보.
2. **리눅스 기반 파이프라인 개발성**: GStreamer 프레임워크 및 Python 개발 라이브러리와의 높은 결합도 확보.
3. **네트워크 대역폭 최적화**: 로컬 무선랜(Wi-Fi) 및 랜선(Ethernet Direct) 전송 지원.
4. **저비용 실현**: 보드 1대당 10~15만원 이하의 빌드 예산 제약 준수.

### [보드 선정 비교 사양표]

| 구분 | Raspberry Pi 4B (본 시스템 채택) | Arduino Mega 2560 | Jetson Nano |
| --- | --- | --- | --- |
| **CPU** | ARM Cortex-A72 (Quad-core) | ATmega2560 (8-bit) | ARM Cortex-A57 (Quad-core) |
| **RAM** | 8GB LPDDR4 | 8KB SRAM | 4GB LPDDR4 |
| **GPU** | Broadcom VideoCore VI | 없음 | 128-core Maxwell |
| **OS 지원** | Linux (Ubuntu Desktop/Debian) | RTOS / Bare-metal | Linux (Ubuntu L4T) |
| **인코더** | H.264 Hardware Encoder | 지원 불가 | H.264/H.265 Hardware |
| **단가** | 약 120,000원 | 약 25,000원 | 약 290,000원 |
| **선정의견** | **최종 채택** (성능 대비 단가 최적) | 탈락 (영상처리 하드웨어 한계) | 탈락 (비용 초과 및 오버스펙) |

---

### 5.2 소프트웨어 상세 설계 명세서 (DSD 3장/SW 부문용)

### [로컬 백업 & 스트리밍 파이프라인 세부 사양]

- **입력 규격 (Capture Source)**: `libcamerasrc` (640x480 resolution, 30 FPS, YUV raw)
- **인코딩 코덱**: `x264enc` (H.264 Software Encoding, Profile: Baseline, Tune: Zerolatency)
- **네트워크 대역폭 세팅**: Target Bitrate 500kbps 제한
- **인터페이스 분기(Tee) 처리**:
    - **분기 1 (로컬 저장)**: `splitmuxsink`를 통한 MPEG-TS 파일 분할 기록. (세그먼트당 10초)
    - **분기 2 (실시간 송출)**: `flvmux` 포맷팅 후 `rtmpsink`를 활용해 내부 MediaMTX(RTMP:1935) 전송. MediaMTX는 이를 RTSP(8554) 규격으로 실시간 가교 중계.

### [RTSP 클라이언트 패키지 통신 및 재연결 파라미터 구조]

PC 수신용 OpenCV 클라이언트의 연결 프로토콜 옵션 세부 명세입니다.

| 항목 (FFmpeg Option) | 자료형 (Type) | 상세 설정값 | 기능 설명 |
| --- | --- | --- | --- |
| **rtsp_transport** | String | `tcp` | 패킷 유실을 차단하여 화면 번짐을 억제하는 신뢰성 전송 방식 강제 지정 |
| **stimeout** | Integer (usec) | `2000000` (2초) | RTSP 초기 연결(Handshake) 협상 타임아웃 제한 시간 |
| **timeout** | Integer (usec) | `2000000` (2초) | 연결 유지 중 프레임 미유입 시 소켓 대기 제한 시간 |
| **CAP_PROP_BUFFERSIZE** | Integer | `1` | 비디오 수신 링 버퍼 크기 최소화로 화면 딜레이 누적 방지 |

### [주요 함수 및 클래스 인터페이스 정의]

### 1. `check_server_port_open(rtsp_url, timeout)`

- **기능 설명**: OpenCV 연결 강제 프리징 현상 방지를 위해 지정된 RTSP 서버의 포트가 현재 실시간 활성화 상태인지 TCP 소켓으로 사전 체크합니다.
- **입출력 인터페이스**:

| 구분 | 변수/반환값 명칭 | 데이터 타입 (Type) | 상세 설명 |
| --- | --- | --- | --- |
| **Input** | `rtsp_url` | String | 접속 대상 RTSP 풀 주소 (예: `rtsp://IP:8554/live`) |
|  | `timeout` | Float | 소켓 응답 대기 한계 초 (Default: 1.5초) |
| **Output** | 반환값 (Return) | Boolean | `True`: 포트 활성화(접속 가능) / `False`: 접속 불가 |

### 2. `RTSPReceiver` 클래스 및 메소드 구조

- **`RTSPReceiver.__init__(rtsp_url, reconnect_interval)`**
    - **Input**: `rtsp_url` (String), `reconnect_interval` (Integer: 기본 3초)
    - **Process**: 내부 플래그(`running`, `is_connected`), 버퍼 변수(`frame`), 락 잠금자(`lock`) 인스턴스를 생성 및 초기화합니다.
- **`RTSPReceiver.start()`**
    - **Process**: 수신용 스레드(`_receive_loop`)와 모니터링 감시견 스레드(`_watchdog_loop`)를 동시 생성하고 `daemon=True` 상태로 병렬 구동시킵니다.
- **`RTSPReceiver._watchdog_loop()`**
    - **Process**: 1.0초 단위 주기로 마지막 수신 시각(`last_frame_time`)을 확인하여 5초 이상 동결 시 `self.cap.release()`로 기존 소켓을 소멸시켜 수신 루프의 재부팅을 강제 유도합니다.
- **`RTSPReceiver._receive_loop()`**
    - **Process**: `check_server_port_open`을 사용해 포트 개방 유무를 우선 선별하며, 이더넷 카드 물리 안정화 지연(1.5초)을 거친 뒤 `cv2.VideoCapture`를 바인딩합니다. 이후 스레드 락을 안전히 통과시키며 전역 프레임 메모리를 최신 데이터로 동적 복제 갱신합니다.
- **`RTSPReceiver.get_frame()`**
    - **Process**: 타 스레드의 접근 충돌을 막기 위해 락을 잡고, 최신 프레임의 완전 복사본(`self.frame.copy()`)을 호출자에게 안전하게 양도합니다.
    - **Output**: `numpy.ndarray` (RGB 이미지 프레임 객체) 또는 `None` (버퍼가 비었을 때)
- **`RTSPReceiver.stop()`**
    - **Process**: 스레드 동작 트리거 플래그(`running`)를 `False`로 낮추고 구동 중인 백그라운드 스레드들이 완전히 자원을 반환하고 종료될 때까지 `join(timeout=3)` 대기 처리합니다.