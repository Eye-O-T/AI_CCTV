# 이벤트 MVP 5단계 통합 검증 — 2026-11-20

**합성 전체 흐름과 사용자가 참여한 단일 웹캠 정상 시나리오 검증을 완료했다.** 다중 카메라·RTSP·다른 사람 구분·장시간 안정성까지 검증했다는 뜻은 아니다. 이벤트 검증을 재실행하거나 결과 범위를 판단할 때 이 문서와 [개발 계획](EVENT_MVP_DEVELOPMENT_PLAN.md)을 읽는다.

## 범위와 보존

- 브랜치 `dev-crop_container`, 제품 코드 기준 커밋 `172e54de`. 작업 시작 시 Git 작업 트리는 깨끗했다. 5단계에서는 제품 코드·모델·품질 기준·API/DB 구조를 수정하지 않고 진단 스크립트와 문서만 추가/갱신했다. 커밋·푸시·브랜치 변경은 하지 않았다.
- `tests/manual_event_mvp_probe.py`는 직접 실행하는 진단 도구다. 제품 실행에 필수인 코드는 아니며 자동 unittest 발견 대상도 아니다.
- 제품 AppController·SessionManager·CCTVMainWindow·CameraRuntimeManager·VideoWorker/QThread·CropWorker·OSNetWorker/공용 Manager·EventCoordinator/HTTP 큐·별도 프로세스 FastAPI를 연결했다. 진단 전용 관리자 계정/DB/저장소를 사용했다. 비밀번호는 실행마다 무작위이며 토큰·비밀번호는 보고서에 저장하지 않는다.
- 운영 `client_code/backend/data/ai_cctv.db`, WAL/SHM, `ai_cctv.storage.json`의 실행 전후 존재 여부/해시를 비교했다. 실제 촬영 A·B와 합성 실행 모두 동일했다. 운영 계정·카메라 등록·DB·저장 설정·기존 미디어를 수정하지 않았다.
- 진단 DB/설정/보고서/계산용 crop: `output/event_mvp_stage5_LABEL/`. 기록 중 파일: `temp/event_mvp_stage5_LABEL/`. 최종 저장소: `verification_media/event_mvp_stage5_LABEL/AI_CCTV/`. 최종 저장소를 프로젝트 output/temp 밖에 두어 백엔드의 저장소 경계 정책도 유지했다. 같은 C: 볼륨만 사용했고 기존 이름은 거부해 앞선 결과를 보존한다.
- 기존 Python 3.11/venv311 패키지를 재사용했다. 설치·업그레이드·가중치 다운로드는 하지 않았다. Ultralytics 설정도 진단 output 안에 격리했다.

## 자동 검증

### 백엔드·집중 회귀

백엔드 전체 88개와 `test_event_camera.py` 14개, `test_event_person.py` 19개를 합쳐 **121개 전부 통과**했다(51.078초, skip 0). 원자료: `output/event_mvp_stage5_automated_20261120.json`.

4단계의 서로 다른 166개 중 이번에 다시 실행하지 않은 나머지 133개는 이전 결과를 재사용했다. 기존 CAP_DSHOW 파일 입력 실패 3개는 이번 121개에 포함하지 않았으며, 해결되거나 성공했다고 처리하지 않는다.

### 합성 종합 실행

`synthetic_20261120_a`에서 제품 창을 offscreen으로 실행하고 입력 영상/탐지/특징 벡터만 대역으로 바꿨다. 카메라를 열지 않았으며 전신·크기·선명도·첫 5장·기본 0.5초 샘플 간격은 바꾸지 않았다. crop 파일 저장부터 나머지 큐/HTTP/DB/미디어/종료는 실제 제품 흐름이다.

1. 첫 실행에서 글로벌 ID 확인 후 합성 탐지를 중단하고 정상 영상은 계속 공급해 자연 사라짐을 확인했다.
2. STOP 후 같은 카메라를 새 실행 UUID로 다시 START했다. 이번에는 인물이 탐지되는 상태에서 STOP해 사라짐을 추가하지 않는 것을 확인했다.
3. 제품 로그아웃을 통해 영상 정리 → 마지막 이벤트/미디어 저장 → 토큰 폐기 순서를 실행하고 자신이 만든 백엔드를 정상 종료했다.

| 검증 항목 | 결과 |
| --- | --- |
| 이벤트 수 | 시작 2 / 중지 2 / 등장 2 / 사라짐 1, 총 7개 |
| 관측 연결 | 실행 UUID 2개, 관련 등장 참조·발생 시각 일치, 중복 없음 |
| API·DB | 시간대 표기를 UTC로 정규화하면 모든 이벤트 필드가 동일 |
| 파일 | MP4 4개 + JPEG 14개, 총 18개 디코딩 성공 |
| 계산용 crop | 2묶음 10장 원본 해시 유지 |
| 종료 | 이벤트 큐 완료, 제품 Access 토큰 거부 확인, 백엔드 종료 코드 0 |
| 저장소 | DB 상대 경로/최종 파일 일치, 사용자 위치에 기록 중 파일 없음 |

같은 합성 벡터를 사용한 Global 1 재연결은 큐/API 결정 흐름 검증이다. 실제 사람의 재식별 정확도나 실카메라 재시작 전후 ID 유지 검증으로 해석하지 않는다.

원자료: `output/event_mvp_stage5_synthetic_20261120_a/report.json`.

## 실제 단일 웹캠·사용자 참여

도구 실행 계정에서 만든 Qt 창은 사용자 바탕화면 창 목록에서 확인되지 않았다. 이 실행(`webcam_20261120_a`)은 START 없이 대기하다가 제한 시간 종료됐으며 **카메라를 열지 않았다**. 해당 보고서는 화면 전달 제약으로 분류하고 장치/이벤트 저장 실패로 해석하지 않는다. 컴퓨터 사용 도구로 제품 버튼을 누르거나 로그인 정보를 입력하지 않았다.

이후 사용자가 자신의 PowerShell에서 진단 도구를 직접 실행하고, 임시 DB로 자동 로그인된 제품 창에서 웹캠 index 0의 START·전신 등장/퇴장을 수행했다. 제품 영상 처리는 실제 YOLO26s·OSNet 기본 프로파일이며 대역을 적용하지 않았다. 품질 기준·5장 수집·유사도 임계값도 그대로다. 도구가 제품 창을 직접 시각 관찰한 검증과 구분하며, Worker/큐/API/DB/파일 기록으로 결과를 확인했다.

| 시도 | 실제 관측 | DB 이벤트 | 판정 |
| --- | --- | --- | --- |
| `webcam_user_20261120_a` | 1280×720, 640프레임, Global 1·crop 5장 | 시작 1 / 등장 1 / 중지 1 | STOP 직전까지 인물 감지. 사라짐 0건은 정상. 자연 퇴장 항목은 미충족이므로 전체 passed=false |
| `webcam_user_20261120_b` | 1280×720, 1053프레임, Global 1·crop 5장 | 시작 1 / 등장 1 / 사라짐 1 / 중지 1 | 자연 사라짐 저장 확인 후 진단 도구가 제품 STOP/로그아웃 호출. 전체 passed=true |

두 시도는 각각 독립적인 진단 DB이므로 둘 다 Global 1인 것을 동일인 유지/재실행 복원 검증으로 해석하지 않는다.

### 통과한 B 시도의 확인 내용

- 실제 수신: 2026-11-20 09:57:13~09:57:54 KST. 첫 프레임 시각과 카메라 시작 occurred_at, 정리 완료 시각과 중지 occurred_at이 정확히 일치한다.
- 등장 시각은 첫 탐지 UTC, 사라짐 시각은 마지막 탐지 UTC로 기록됐다. 사라짐은 같은 실행/세대/로컬/글로벌 ID의 등장을 `related_event_id`로 참조하며 미디어는 NULL이다.
- 등장에 저장된 crop/clip/trajectory 3개 상대 경로는 모두 `시작시각~마지막관측시각`의 같은 최종 폴더에 있고 실제 파일이 존재한다. 원본 녹화와 인물 클립 MP4 총 5개 및 JPEG 7개를 디코딩했다. JPEG 7개는 계산용 5장·대표 crop·궤적이다.
- 계산용 crop 5장 원본 해시가 유지됐다. 최종 저장소에는 `.pending`/기록 중 `recording.mp4`가 없었고, 글로벌 ID 확정 관측의 최종 경로와 DB 경로가 일치했다.
- Worker cleanup_ok=true, 이벤트 저장 실패 0건·큐 마무리 완료, 제품 로그아웃 토큰 무효화, 소유 백엔드 종료 코드 0을 확인했다. 운영 DB/저장 설정 해시도 동일했다.
- A 시도도 MP4 4개·JPEG 7개 디코딩, 미디어 경로 갱신·토큰 폐기·정상 종료는 통과했다. 사람이 감지된 상태에서 STOP하면 사라짐을 만들지 않는 실장치 사례로 보존한다.

원자료: `output/event_mvp_stage5_webcam_user_20261120_{a,b}/report.json`. 세 성공/부분 시도의 API와 DB 전체 필드, 관련 등장 참조, 카메라 시각을 별도로 재검토한 결과는 `output/event_mvp_stage5_review_20261120.json`에 있다. 최초 도구 계정의 화면 대기 기록도 별도 보고서로 보존했다.

## 재실행

실행 디렉터리: `C:\GitHub_Clone\AI_CCTV`. 운영 프로그램과 다른 카메라 사용 프로그램을 종료한 뒤 실행한다. 아래 LABEL은 기존 것과 다른 이름을 사용한다.

```powershell
.\venv311\Scripts\python.exe -B client_code/tests/manual_event_mvp_probe.py --mode synthetic --label synthetic_NEW
.\venv311\Scripts\python.exe -B client_code/tests/manual_event_mvp_probe.py --mode webcam --index 0 --label webcam_NEW --auto-finish-after-disappearance
```

가상환경 실행기가 원래 Python 경로를 찾지 못하는 환경에서 이번에 사용한 대체 명령:

```powershell
$env:PYTHONPATH = "$PWD\venv311\Lib\site-packages;$env:PYTHONPATH"
& "$env:USERPROFILE\.local\bin\python3.11.exe" -B client_code/tests/manual_event_mvp_probe.py --mode webcam --index 0 --label webcam_NEW --auto-finish-after-disappearance
```

webcam 모드는 창이 열려도 START 전에는 카메라를 열지 않는다. START → 전신이 보이도록 대기 → Global ID 확인 → 화면 밖으로 완전히 이동 후 대기한다. 위 옵션에서는 자연 사라짐 저장을 확인한 후 자동 STOP·로그아웃한다. 아직 조건이 충족되지 않으면 STOP하지 않고 기다리며, 최대 대기 시간은 기본 240초다. 자동 마무리 옵션을 빼면 사용자가 STOP·로그아웃한다.

진단 폴더는 삭제하지 않는다. `report.json`의 passed=false는 개별 checks와 함께 해석하며, 관측/품질 조건 미충족을 이벤트 저장 실패나 성공으로 바꾸지 않는다.

## 남은 제한

- 단일 웹캠과 한 명의 정상 등장/퇴장 범위다. 두 물리 카메라·RTSP 장애/재연결·서로 다른 사람/동시 인물의 정확도·실제 프로그램 재시작 전후 글로벌 ID 유지·장시간 부하는 미검증이다.
- 디코딩 성공은 무손실 녹화나 실제 시간과 재생 길이의 정확한 일치를 뜻하지 않는다. 지속 FPS·GPU 메모리/큐 적체·전체 지연은 계측하지 않았다. 이번 보고서에는 실제 OSNet 장치 이름도 수집하지 않았으므로 특정 GPU에서의 성능 통과를 주장하지 않는다.
- 미확정 글로벌 ID 관측은 이력에서 제외된다. 파일/DB는 하나의 원자적 트랜잭션이 아니며 저장 실패·강제 종료·시간 초과에 대한 영속 복구는 없다.
- PyQt 이벤트 이력 UI·Flutter·알림/푸시·파일 송출/HTTP Range는 이번 MVP 범위 밖이며 추가하지 않았다.
