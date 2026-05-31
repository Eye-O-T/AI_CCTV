import os
import zipfile
import tempfile
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="AI CCTV RPi Backup API Server")

# 백업 디렉터리 경로 설정 (사용자 RPi 환경에 맞춰 홈 폴더의 backups를 가리키도록 설정)
BACKUP_DIR = os.path.join(os.path.expanduser("~"), "backups")


def remove_temp_file(path: str):
    """파일 전송 완료 후 임시 압축파일을 삭제하는 헬퍼 함수"""
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"[Backup Server] 임시 파일 삭제 완료: {path}")
    except Exception as e:
        print(f"[Backup Server] 임시 파일 삭제 중 에러 발생: {e}")


@app.get("/recover")
def recover_backups(start: str, end: str, background_tasks: BackgroundTasks):
    """
    지정한 시간대 (start ~ end) 사이의 누락된 .ts 파일들을 ZIP으로 묶어서 반환합니다.
    - start: ISO 8601 형식 (예: 2026-05-30T21:00:15)
    - end: ISO 8601 형식 (예: 2026-05-30T21:00:25)
    """
    # 1. 입력 시각 파싱
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="시작 시각(start) 및 종료 시각(end)은 ISO 8601 형식(예: YYYY-MM-DDTHH:MM:SS)이어야 합니다."
        )

    if start_dt > end_dt:
        raise HTTPException(
            status_code=400,
            detail="시작 시각이 종료 시각보다 늦을 수 없습니다."
        )

    # 2. 백업 디렉터리 확인
    if not os.path.exists(BACKUP_DIR):
        return JSONResponse(
            status_code=404,
            content={"message": f"서버에 백업 디렉터리({BACKUP_DIR})가 존재하지 않습니다."}
        )

    # 3. 백업 파일 탐색 및 필터링
    target_files = []
    
    try:
        files = os.listdir(BACKUP_DIR)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"백업 폴더 읽기 실패: {e}")

    for file in files:
        if not file.endswith(".ts"):
            continue
            
        filepath = os.path.join(BACKUP_DIR, file)
        try:
            # 파일 수정 시간(mtime) 획득 및 datetime 변환
            mtime_timestamp = os.path.getmtime(filepath)
            file_end_time = datetime.fromtimestamp(mtime_timestamp)
            # 녹화 단위는 10초 분량이므로 시작 시각은 (종료 시각 - 10초)로 계산
            file_start_time = datetime.fromtimestamp(mtime_timestamp - 10.0)
            
            # 클라이언트 누락 구간과 파일 비디오의 녹화 시간 구간이 겹치는지 체크
            # 겹침 조건: max(start_dt, file_start_time) < min(end_dt, file_end_time)
            overlap_start = max(start_dt, file_start_time)
            overlap_end = min(end_dt, file_end_time)
            
            if overlap_start < overlap_end:
                target_files.append((filepath, file))
        except Exception as e:
            print(f"[Warning] 파일 정보 확인 중 오류 발생 ({file}): {e}")
            continue

    # 4. 필터링된 파일 개수 검증
    if not target_files:
        return JSONResponse(
            status_code=404,
            content={"message": "해당 시간대에 해당하는 백업 비디오 조각이 존재하지 않습니다."}
        )

    print(f"[Backup Server] 누락 복구 대상 파일 {len(target_files)}개 감지.")

    # 5. 임시 ZIP 파일 생성
    try:
        # 시스템 임시 디렉터리에 ZIP 파일 생성
        temp_dir = tempfile.gettempdir()
        zip_filename = f"recovered_backup_{int(time.time())}.zip"
        temp_zip_path = os.path.join(temp_dir, zip_filename)
        
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for filepath, filename in target_files:
                zip_file.write(filepath, arcname=filename)
                
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"서버에서 ZIP 파일 생성 중 오류 발생: {e}"
        )

    # 6. 다운로드 응답 전송 및 전송 완료 후 백그라운드 태스크로 임시 ZIP 파일 삭제 예약
    background_tasks.add_task(remove_temp_file, temp_zip_path)
    
    return FileResponse(
        path=temp_zip_path,
        media_type="application/x-zip-compressed",
        filename="recovered_backups.zip"
    )


if __name__ == "__main__":
    import uvicorn
    # 포트는 8002로 지정 (자원 모니터링 서버가 8001을 쓰므로 피함)
    uvicorn.run(app, host="0.0.0.0", port=8002)
