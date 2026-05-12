# 알림 전송 시스템 도입 논의 자료

> 검토 기준일: 2026-05-12 KST  
> 검토 대상: 실시간 탐지 이벤트를 운영자 또는 프로젝트 참여자에게 전달하는 내부 알림 파이프라인  
> 핵심 Payload: 10초 내외 영상 클립 1개, 탐지 결과 이미지 1장, 상황 설명 텍스트 1줄

## 1. 결론 요약

본 프로젝트의 1차 도입안은 **Discord Bot 기반 알림 전송 시스템**으로 한다. 카카오톡 개인용 REST 메시지 API는 한국 사용자 접근성은 높지만, 본 프로젝트가 요구하는 **짧은 영상 클립 + 이미지 + 텍스트를 즉시 확인하는 알림**에는 구조적으로 맞지 않는다.

## 2. 의사결정 표

| 항목 | KakaoTalk Message API | Discord Bot API |
|---|---:|---:|
| 텍스트 1줄 전송 | 가능 | 가능 |
| 이미지 직접 첨부 | 제한적. URL 또는 카카오 업로드 URL 기반 | 가능. `files[n]` / `discord.File` 첨부 |
| 10초 영상 클립 직접 첨부 | 부적합. 템플릿 메시지 구조상 파일 첨부형 전송이 아님 | 가능. 단, 파일별 10MiB 안전 기준 필요 |
| 별도 미디어 호스팅 필요성 | 높음 | 낮음 |
| 메시지 형식 자유도 | 낮음. 기본/사용자 정의 템플릿 중심 | 높음. 텍스트, 첨부, embed 조합 가능 |
| 수신 대상 제약 | 강함. 동일 서비스 사용자·친구·권한·쿼터 제약 | Discord 서버/채널 권한 중심 |
| 라이브러리/생태계 | PyKakao는 보조적 래퍼로 볼 것 | discord.py가 현재도 활발히 배포됨 |
| 본 프로젝트 적합성 | 보류 또는 예외적 보조 채널 | 1차 도입 권장 |

## 3. 왜 Discord를 1차 도입안으로 보는가

Discord는 알림 메시지에 로컬 파일을 직접 첨부할 수 있다. `discord.py` 문서도 `discord.File`을 사용한 단일·다중 파일 업로드 예제를 제공한다. 즉, 탐지 모듈이 생성한 이미지와 MP4 클립을 별도 외부 CDN 없이 같은 메시지에 첨부하는 MVP를 빠르게 만들 수 있다.

다만 운영 기준은 보수적으로 잡아야 한다. Discord의 파일 업로드 문서는 파일별 기본 제한을 10MiB로 설명하고, 메시지 생성 API는 메시지 전송 요청 최대 크기를 25MiB로 설명한다. 따라서 영상 클립은 **9MiB 이하**를 목표로 인코딩하고, 이미지까지 포함한 전체 multipart 요청은 **24MiB 이하**를 목표로 둔다.

## 4. KakaoTalk을 1차 도입안에서 제외하는 이유

KakaoTalk Message API는 카카오 로그인 사용자 간 상호작용 또는 “나에게 보내기” 성격에 가깝다. 카카오 공식 문서도 Kakao Talk Share와 Kakao Talk Message가 모두 사용자 간 메시징이며, 서비스가 사용자에게 직접 메시지를 보내는 구조가 아니라고 설명한다. 또한 메시지는 템플릿을 중심으로 구성되며, 이미지도 파일 자체가 아니라 URL 또는 카카오 업로드 API가 발급한 URL을 사용해야 한다.

본 프로젝트는 탐지 이벤트 발생 직후 운영자가 미디어를 바로 확인해야 한다. 이 요구사항에서는 KakaoTalk API를 사용하면 미디어 호스팅, URL 접근성, 템플릿 구성, OAuth 토큰 관리, 쿼터 관리가 모두 추가된다. 따라서 “텍스트 또는 링크 중심 보조 채널”로는 가능하지만, “영상+이미지 중심의 1차 알림 채널”로는 부적합하다.

## 5. 권장 아키텍처

```text
[Detection Process]
  - 객체 탐지/영상 처리
  - 이미지와 10초 클립 생성
  - NotificationPayload 생성
        |
        | multiprocessing.Queue / Redis Stream / RabbitMQ
        v
[Notification Process]
  - discord.py 이벤트 루프
  - 파일 크기 검증
  - 재시도·백오프·중복 방지
  - Discord 채널 전송
        |
        v
[Discord Channel / Thread]
```

### 5.1 프로세스 분리 원칙

영상 처리와 객체 탐지는 CPU/GPU 또는 I/O 부하가 크고 동기 코드가 많다. 반면 Discord 봇은 `asyncio` 기반 비동기 이벤트 루프에서 동작한다. Python 공식 문서도 CPU-bound blocking code를 이벤트 루프에서 직접 호출하면 다른 asyncio 작업과 I/O가 지연된다고 설명한다. 따라서 탐지 프로세스와 알림 프로세스는 분리한다.

### 5.2 IPC 선택지

| 선택지 | 권장 상황 | 장점 | 단점 |
|---|---|---|---|
| `multiprocessing.Queue` | 단일 장비 MVP | 구현이 단순함 | 재시작 후 미처리 이벤트 보존이 약함 |
| SQLite spool + Queue | 단일 장비에서 유실 방지 필요 | 재시도·복구 구현 가능 | 구현 복잡도 증가 |
| Redis Stream | 여러 프로세스/장비, 재처리 필요 | consumer group, ack, pending 처리 가능 | Redis 운영 필요 |
| RabbitMQ | 운영형 메시지 브로커 필요 | 라우팅·ack·내구성 좋음 | 운영 부담 증가 |

MVP는 `multiprocessing.Queue`로 시작하되, 알림 유실이 운영 리스크가 되면 Redis Stream 또는 RabbitMQ로 전환한다.

## 6. 제안 Payload 스키마

```json
{
  "event_id": "uuid-v4",
  "event_time": "2026-05-12T14:30:00+09:00",
  "severity": "warning",
  "text": "작업자 접근 금지 구역 진입 감지",
  "image_path": "artifacts/20260512/event_001.jpg",
  "clip_path": "artifacts/20260512/event_001.mp4",
  "dedup_key": "camera-03:restricted-zone:20260512T143000",
  "ttl_seconds": 300
}
```

필수 검증 항목은 다음과 같다.

| 필드 | 검증 |
|---|---|
| `event_id` | UUID 또는 단조 증가 ID. 재시도 중복 전송 방지 |
| `text` | 1줄 요약. Discord `content` 2,000자 제한보다 훨씬 작게 유지 |
| `image_path` | 존재 여부, 확장자, 크기. 권장 2MiB 이하 |
| `clip_path` | 존재 여부, 확장자, 크기. 권장 9MiB 이하 |
| `ttl_seconds` | 너무 오래된 탐지 이벤트 폐기 |

## 7. Discord 전송 기준

권장 인코딩 정책은 다음과 같다.

```bash
ffmpeg -y -i input.mp4 \
  -t 10 \
  -vf "scale='min(1280,iw)':-2" \
  -c:v libx264 -preset veryfast -crf 28 \
  -an \
  output_clip.mp4
```

운영 기준은 다음을 만족해야 한다.

| 항목 | 기준 |
|---|---:|
| 영상 파일 크기 | 9MiB 이하 목표 |
| 이미지 파일 크기 | 2MiB 이하 목표 |
| 전체 메시지 요청 | 24MiB 이하 목표 |
| 전송 실패 재시도 | 429는 `retry_after`, 5xx는 지수 백오프 |
| 권한 | View Channel, Send Messages, Attach Files 최소 권한 |
| 토큰 | `.env` 또는 secret manager. Git 커밋 금지 |

## 8. 검증 계획

1. 10초 샘플 클립 3종을 만든다: 480p, 720p, 1080p.
2. 각 클립과 이미지 1장을 동일 메시지로 첨부 전송한다.
3. 파일 크기 초과, 권한 부족, rate limit, 네트워크 실패를 각각 재현한다.
4. 탐지 프로세스가 1분에 10건 이상 이벤트를 생성할 때 알림 프로세스가 이벤트 루프를 막지 않는지 확인한다.
5. 봇 토큰이 로그, 예외 메시지, Git history에 남지 않는지 확인한다.

## 9. 파일 구성

- [`kakaotalk.md`](./kakaotalk.md): KakaoTalk Message API의 제약, 미디어 전송 구조, 쿼터, 도입 보류 사유
- [`discord.md`](./discord.md): Discord Bot API의 장점, 최신 첨부 제한, 구현 기준, 실패 모드, PoC 코드 방향
- `readme.md`: 전체 의사결정 요약 및 공통 아키텍처

## 10. 참고 자료

### Discord

- Discord Developer Docs — Uploading Files: https://docs.discord.com/developers/reference#uploading-files
- Discord Developer Docs — Message Resource / Create Message: https://docs.discord.com/developers/resources/message#create-message
- Discord Support — File Attachments FAQ: https://support.discord.com/hc/en-us/articles/25444343291031-File-Attachments-FAQ
- Discord Developer Docs — Rate Limits: https://docs.discord.com/developers/topics/rate-limits
- discord.py Docs — FAQ / Uploading image or files: https://discordpy.readthedocs.io/en/stable/faq.html#how-do-i-upload-an-image
- PyPI — discord.py: https://pypi.org/project/discord.py/

### Kakao

- Kakao Developers — Kakao Talk Message Concepts: https://developers.kakao.com/docs/en/kakaotalk-message/common
- Kakao Developers — Kakao Talk Message REST API: https://developers.kakao.com/docs/ko/kakaotalk-message/rest-api
- Kakao Developers — Message Template Common: https://developers.kakao.com/docs/en/message-template/common
- Kakao Developers — Quota: https://developers.kakao.com/docs/en/getting-started/quota
- Kakao Developers — Error Code: https://developers.kakao.com/docs/en/rest-api/error-code
- PyPI — PyKakao: https://pypi.org/project/PyKakao/

### Python architecture

- Python Docs — Developing with asyncio / Running Blocking Code: https://docs.python.org/3/library/asyncio-dev.html#running-blocking-code
- Python Docs — multiprocessing Queue and Pipe: https://docs.python.org/3/library/multiprocessing.html
- Python Docs — asyncio Queue: https://docs.python.org/3/library/asyncio-queue.html
