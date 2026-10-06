# PC CCTV 백엔드

현재 `main.py`에 최소 FastAPI 앱과 `GET /health`가 구현되어 있다. DB 연결과 인증·카메라·이벤트 API는 아직 구현하지 않았다. 다음 단계는 인증 API 구현 및 검증 후 PyQt 클라이언트 연결이다.

```text
backend/
├─ __init__.py
├─ main.py           # FastAPI 앱 생성·라우터 등록·시작/종료
├─ database.py       # SQLite 연결·DB 세션
├─ dependencies.py   # DB 세션·현재 사용자 의존성
├─ security.py       # 비밀번호 해시·토큰 생성/검증
├─ routers/          # Controller: HTTP 요청·응답
├─ services/         # 업무 규칙·트랜잭션 경계
├─ repositories/     # DB 접근
├─ models/           # Entity: SQLAlchemy 테이블 모델
└─ schemas/          # DTO: Pydantic 요청·응답 모델
```

## 구현 기준

- [백엔드 스키마 및 제약사항](../docs/BACKEND_SCHEMA.md)을 참고한다.
- API 구현·클라이언트 연동·검증 전에는 [통합 API 명세서](../docs/API_SPEC.md)를 읽는다. 현재 인증 API 계약이 정리되어 있으며, 이후 카메라·이벤트 명세도 같은 파일 하단에 추가한다.
- 요청 처리는 Router → Service → Repository 순서로 나눈다.
- DB 모델과 API 요청·응답 모델을 분리한다.
- 백엔드는 PyQt 화면이나 YOLO·CNN 모델을 import하지 않는다.
- 관제 프로그램은 HTTP로 백엔드에 접근하며 Repository를 직접 호출하지 않는다.
- 백엔드는 별도 프로세스로 실행하고, 관제 프로그램에서 시작·종료를 관리할 예정이다.
- Refresh 토큰을 메모리에서 관리하므로 초기 백엔드는 단일 프로세스를 전제로 한다.
- 실제 기능과 추가 파일은 사용자가 요청한 개발 단계에서 구현한다.
