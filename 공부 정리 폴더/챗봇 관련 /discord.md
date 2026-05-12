# Discord Bot API 검토

> 검토 기준일: 2026-05-12 KST  
> 결론: 본 프로젝트의 1차 알림 채널로 **도입 권장**. 단, 첨부 용량 기준을 최신 문서 기준으로 보수적으로 적용해야 한다.

## 1. 검토 범위

이 문서는 Discord Bot API와 Python 라이브러리 `discord.py`를 기반으로, 탐지 이벤트 알림을 Discord 채널로 전송하는 방안을 검토한다.

본 프로젝트의 요구 Payload는 다음과 같다.

| Payload | 요구사항 |
|---|---|
| 영상 | 10초 내외 탐지 클립 1개 |
| 이미지 | 탐지 결과 이미지 1장 |
| 텍스트 | 상황 설명 1줄 |
| 전달 방식 | 이벤트 발생 직후 운영자 채널에서 바로 확인 |

## 2. 공식 문서 기준 핵심 사실

### 2.1 파일 첨부 가능

Discord Message API는 메시지 생성 시 `files[n]` multipart 파일 필드를 제공한다. `discord.py`도 `discord.File` 객체를 사용해 단일 파일 또는 다중 파일을 업로드하는 방법을 문서화하고 있다.

따라서 본 프로젝트는 탐지 프로세스가 생성한 `event.jpg`와 `event.mp4`를 같은 Discord 메시지에 첨부하는 구조로 구현할 수 있다.

### 2.2 첨부 용량 기준 

Discord 공식 문서 기준으로 구분해야 할 값은 다음과 같다.

| 항목 | 기준 |
|---|---:|
| 파일별 기본 업로드 제한 | 10MiB |
| 메시지 전송 요청 최대 크기 | 25MiB |
| 일반 사용자 비 Nitro 업로드 제한 | 10MB |

따라서 구현 기준은 다음처럼 잡는다.

```text
clip.mp4  <= 9MiB 권장
image.jpg <= 2MiB 권장
multipart 전체 요청 <= 24MiB 권장
```

서버 Boost나 계정 상태에 따라 업로드 한도가 달라질 수 있지만, 운영 안정성을 위해 기본 10MiB를 기준으로 설계한다. 특히 봇 운영에서는 “현재 채널에서 실제로 업로드 가능한 최대값”을 PoC에서 반드시 확인해야 한다.

### 2.3 텍스트 및 미디어 포맷

Discord Message API의 `content` 필드는 최대 2,000자다. 본 프로젝트는 1줄 상황 설명만 요구하므로 충분하다.

Discord Support 문서는 일반적인 첨부 파일 예시로 JPEG, PDF, MP3, MOV, MP4 등을 들고, MP4는 H.264, HEVC/H.265, AV1 인코딩 예시를 제시한다. 운영 호환성을 고려하면 MP4/H.264가 가장 무난하다.

## 3. 도입 장점

| 장점 | 설명 |
|---|---|
| 미디어 직접 첨부 | 이미지와 클립을 같은 메시지에 첨부 가능 |
| 외부 호스팅 불필요 | 용량 제한 내에서는 CDN, presigned URL 없이 구현 가능 |
| 빠른 MVP | Bot 생성, 토큰 발급, 서버 초대, 채널 ID 지정으로 시작 가능 |
| Python 생태계 | `discord.py`가 async/await, rate limit handling, 파일 전송 예제를 제공 |
| 운영자 UX | Discord 채널/스레드/멘션/권한으로 운영 알림 흐름 구성 가능 |

## 4. 구현 방식 선택: Bot vs Webhook

Discord에는 봇과 웹훅 두 가지 주요 전송 방식이 있다.

| 방식 | 적합한 경우 | 장점 | 단점 |
|---|---|---|---|
| Webhook | 단방향 알림만 필요 | 구현이 가장 단순함 | 명령, ack, 상태 조회 등 상호작용이 제한적 |
| Bot | 알림 + 상호작용 필요 | ack, 재전송, 상태 조회, 라우팅 확장 가능 | 봇 프로세스와 토큰 관리 필요 |

본 프로젝트 문맥에서는 “챗봇”을 언급하고 있으므로 Bot을 기본안으로 둔다. 다만 완전한 단방향 MVP라면 Webhook도 실험해 볼 가치가 있다.

## 5. 권장 아키텍처

```text
[Detector]
  - frame capture
  - object detection
  - image/clip write
  - NotificationPayload enqueue
        |
        | IPC
        v
[Discord Notifier]
  - payload dequeue
  - file existence check
  - file size check
  - channel.send(..., files=[...])
  - retry/backoff
```

### 5.1 프로세스 분리

`discord.py`는 `asyncio` 기반으로 동작한다. Python 공식 문서는 CPU-bound blocking code를 이벤트 루프에서 직접 호출하면 모든 concurrent asyncio task와 I/O가 지연될 수 있다고 설명한다.

따라서 객체 탐지 및 영상 처리 코드는 Discord 봇 이벤트 루프와 같은 프로세스에 직접 묶지 않는다. 다음 둘 중 하나를 사용한다.

1. 탐지 모듈과 알림 모듈을 별도 프로세스로 분리하고 IPC Queue 사용
2. 부득이하게 같은 프로세스에서 돌릴 경우 CPU-bound 작업은 `ProcessPoolExecutor` 또는 별도 프로세스로 분리

MVP에서는 `multiprocessing.Queue`가 가장 단순하다. Python `multiprocessing.Queue`는 pipe와 lock/semaphore를 사용해 프로세스 간 공유 큐를 제공한다.

## 6. 최소 권한

봇 초대 시 최소 권한은 다음을 권장한다.

| 권한 | 필요성 |
|---|---|
| View Channel | 대상 채널 접근 |
| Send Messages | 알림 텍스트 전송 |
| Attach Files | 이미지/영상 첨부 |
| Read Message History | 재시도·스레드·상태 확인이 필요한 경우 |
| Mention Everyone | 기본적으로 비권장. 필요한 역할 멘션만 허용 |

관리자 권한은 PoC 편의상 사용할 수 있지만, 공유 레포지토리와 운영 환경에서는 최소 권한으로 낮춘다.

## 7. 환경 변수

```env
DISCORD_BOT_TOKEN=replace-me
DISCORD_CHANNEL_ID=123456789012345678
ALERT_MAX_FILE_MIB=9
ALERT_MAX_REQUEST_MIB=24
```

토큰은 `.env`에 두되, `.env`는 반드시 `.gitignore`에 포함한다. Git history에 토큰이 한 번이라도 커밋되면 Discord Developer Portal에서 즉시 재발급한다.

## 8. 알림 전송 예시 코드 방향

아래 코드는 설계 방향을 설명하기 위한 최소 예시다. 실제 구현에서는 파일 크기 검증, 재시도, 로그 마스킹, payload schema 검증을 추가해야 한다.

```python
from pathlib import Path
import discord

async def send_detection_alert(
    client: discord.Client,
    channel_id: int,
    text: str,
    image_path: str,
    clip_path: str,
) -> None:
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)

    image = Path(image_path)
    clip = Path(clip_path)

    files = [
        discord.File(image, filename=image.name),
        discord.File(clip, filename=clip.name),
    ]

    await channel.send(
        content=text,
        files=files,
        silent=False,
    )
```

## 9. 파일 크기 검증 정책

전송 전에 다음 검증을 수행한다.

```python
from pathlib import Path

MIB = 1024 * 1024


def validate_alert_files(image_path: str, clip_path: str) -> None:
    image = Path(image_path)
    clip = Path(clip_path)

    for p in (image, clip):
        if not p.exists():
            raise FileNotFoundError(p)
        if not p.is_file():
            raise ValueError(f"not a file: {p}")

    if clip.stat().st_size > 9 * MIB:
        raise ValueError("clip too large for safe Discord upload target")

    if image.stat().st_size > 2 * MIB:
        raise ValueError("image too large for safe Discord upload target")

    total = clip.stat().st_size + image.stat().st_size
    if total > 24 * MIB:
        raise ValueError("multipart request likely too large")
```

## 10. 영상 인코딩 기준

10초 클립은 탐지 확인용이므로, 고화질 보존보다 빠른 전송과 안정적인 업로드가 중요하다.

권장 기준은 다음과 같다.

| 항목 | 권장값 |
|---|---|
| 컨테이너 | MP4 |
| 비디오 코덱 | H.264 |
| 길이 | 10초 이하 |
| 해상도 | 720p 이하 우선 |
| 오디오 | 제거 권장 |
| 크기 | 9MiB 이하 |

예시 명령:

```bash
ffmpeg -y -i input.mp4 \
  -t 10 \
  -vf "scale='min(1280,iw)':-2" \
  -c:v libx264 -preset veryfast -crf 28 \
  -an \
  output_clip.mp4
```

카메라 품질이나 움직임이 많아 9MiB를 넘는 경우에는 다음 순서로 낮춘다.

1. FPS 낮추기: `-r 15`
2. 해상도 낮추기: 720p → 480p
3. CRF 높이기: 28 → 30 또는 32
4. 알림에는 이미지와 텍스트만 보내고, 영상은 별도 저장소 링크로 전환

## 11. Rate limit 대응

Discord 공식 문서는 per-route와 global rate limit을 구분하며, rate limit 값은 변경될 수 있으므로 하드코딩하지 말고 응답 헤더를 파싱하라고 안내한다. 또한 모든 봇은 글로벌 기준으로 초당 50 요청까지 가능하다고 설명한다.

`discord.py`는 rate limit handling을 제공하지만, 본 프로젝트에서도 다음 방어 로직을 둔다.

| 상황 | 대응 |
|---|---|
| 429 Too Many Requests | `retry_after`를 존중하고 재시도 |
| 5xx | 지수 백오프 후 재시도 |
| 401/403 | 토큰 또는 권한 문제. 자동 무한 재시도 금지 |
| 413 Payload Too Large | 재인코딩 또는 이미지/영상 분리 전송 |
| 채널 ID 오류 | 설정 검증 실패로 처리 |

탐지 이벤트가 폭주할 경우에는 동일 카메라·동일 객체·동일 구역 기준으로 dedup 또는 cooldown을 적용한다.

## 12. 운영 리스크와 대응

| 리스크 | 대응 |
|---|---|
| 봇 토큰 유출 | `.env`/secret manager 사용, Git 커밋 금지, 유출 시 즉시 rotate |
| 첨부 용량 초과 | 전송 전 파일 크기 검증 및 자동 재인코딩 |
| 이벤트 루프 blocking | 탐지 프로세스와 알림 프로세스 분리 |
| 알림 폭주 | queue maxsize, cooldown, severity filter 적용 |
| 메시지 유실 | Redis Stream 또는 SQLite spool 도입 |
| 권한 변경 | 시작 시 채널 접근 및 Attach Files 권한 self-test |
| Discord 장애 | 로컬 spool에 저장 후 복구 시 재전송 또는 보조 채널 사용 |

## 13. PoC 체크리스트

- [ ] Discord Developer Portal에서 앱과 봇 생성
- [ ] 봇 토큰 발급 후 secret으로 저장
- [ ] Guild install scope에 `bot` 포함
- [ ] 대상 채널에 최소 권한 부여: View Channel, Send Messages, Attach Files
- [ ] 480p/720p/1080p 샘플 클립 전송 테스트
- [ ] 10MiB 초과 파일 전송 실패 케이스 확인
- [ ] 전체 multipart 25MiB 근처 실패 케이스 확인
- [ ] 429 발생 시 재시도 동작 확인
- [ ] 탐지 프로세스와 알림 프로세스 분리 실행 확인
- [ ] 종료 시 queue drain 또는 spool 저장 확인

## 14. 본 프로젝트 기준 판단

Discord Bot API는 본 프로젝트의 1차 알림 매체로 적합하다.

단, 도입 결정 문서에는 다음 표현을 사용한다.

> Discord는 이미지와 짧은 영상 클립을 직접 첨부할 수 있어 본 프로젝트의 알림 Payload에 적합하다. 다만 최신 Discord 공식 문서 기준으로 파일별 기본 업로드 제한은 10MiB이며, 메시지 전송 요청 최대 크기는 25MiB이므로, 클립 인코딩 정책과 전송 전 용량 검증이 필수다.

## 15. 참고 자료

- Discord Developer Docs — Uploading Files: https://docs.discord.com/developers/reference#uploading-files
- Discord Developer Docs — Message Resource / Create Message: https://docs.discord.com/developers/resources/message#create-message
- Discord Developer Docs — Rate Limits: https://docs.discord.com/developers/topics/rate-limits
- Discord Support — File Attachments FAQ: https://support.discord.com/hc/en-us/articles/25444343291031-File-Attachments-FAQ
- Discord Developer Docs — Bots & Companion Apps Overview: https://docs.discord.com/developers/bots/overview
- Discord Developer Docs — Building your first Discord Bot: https://docs.discord.com/developers/quick-start/getting-started
- discord.py Docs — Welcome: https://discordpy.readthedocs.io/en/stable/
- discord.py Docs — FAQ / Uploading files: https://discordpy.readthedocs.io/en/stable/faq.html#how-do-i-upload-an-image
- PyPI — discord.py: https://pypi.org/project/discord.py/
- Python Docs — Developing with asyncio / Running Blocking Code: https://docs.python.org/3/library/asyncio-dev.html#running-blocking-code
- Python Docs — multiprocessing Queue and Pipe: https://docs.python.org/3/library/multiprocessing.html
