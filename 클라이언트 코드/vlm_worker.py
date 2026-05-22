# vlm_worker.py

import threading
import queue
from vlm_person_analyzer_Qwen_test import PersonAnalyzer
from chat_bot import chat_bot as chatbot

class VLMWorker:
    def __init__(self, state_manager):
        self.state_manager = state_manager
        self.task_queue = queue.Queue()
        self.running = False
        self.thread = None
        self.analyzer = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def add_task(self, person_id, crop_path):
        self.task_queue.put((person_id, crop_path))

    def _run(self):
        print("VLM 모델 로딩 중...")
        self.analyzer = PersonAnalyzer()
        print("VLM 모델 로딩 완료")

        while self.running:
            try:
                person_id, crop_path = self.task_queue.get(timeout=1)
            except queue.Empty:
                continue

            try:
                print(f"ID {person_id} VLM 분석 시작: {crop_path}")

                result = self.analyzer.analyze(crop_path)

                self.state_manager.mark_vlm_done(person_id, result)

                print(f"ID {person_id} VLM 분석 결과:")
                print(result)
                chatbot.send_msg(result)
                
            except Exception as e:
                print(f"ID {person_id} VLM 분석 실패: {e}")

            finally:
                self.task_queue.task_done()

    def stop(self):
        self.running = False

        if self.thread is not None:
            self.thread.join(timeout=2)