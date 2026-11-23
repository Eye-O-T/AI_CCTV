"""5단계 영상 라벨과 현재 관측 연결을 합성 프레임/실제 작업 스레드로 검증한다."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from test_reid_pipeline import Api, Extractor
from reid.pipeline import ReIDPipeline
from reid.tasks import MatchResult, Observation
from workers.video_worker import VideoWorker


class ReIDLabelTests(unittest.TestCase):
    def setUp(self):
        self.video = VideoWorker(camera_id=1)
        self.addCleanup(self.video.deleteLater)
        self.frame = np.full((240, 320, 3), 80, dtype=np.uint8)

    def labels(self, status='FULL', bbox=(20, 100, 200, 220)):
        with patch('workers.video_worker.cv2.putText', wraps=cv2.putText) as draw:
            self.video._draw_person_label(self.frame, bbox, 1, status, .97, (0, 255, 0))
        return [call.args[1] for call in draw.call_args_list]

    def test_unassigned_and_assigned_ids_are_distinct_from_detection_score(self):
        self.assertEqual(self.labels(), ['Local:1 Global:--', 'FULL Det:0.97'])
        self.video.global_person_ids[1] = 12
        self.assertEqual(self.labels(), ['Local:1 Global:12', 'FULL Det:0.97'])
        # 전신이 일부 잘려도 같은 활성 관측의 확정된 글로벌 ID는 유지한다.
        self.assertEqual(self.labels('PARTIAL'), ['Local:1 Global:12', 'PARTIAL Det:0.97'])

    def test_label_background_fits_right_edge(self):
        self.video.global_person_ids[1] = 123456
        with patch('workers.video_worker.cv2.rectangle', wraps=cv2.rectangle) as draw:
            self.labels(bbox=(310, 5, 319, 239))
        first, second = draw.call_args.args[1:3]
        self.assertGreaterEqual(first[0], 0)
        self.assertGreaterEqual(first[1], 0)
        self.assertLess(second[0], self.frame.shape[1])
        self.assertLess(second[1], self.frame.shape[0])

    def test_expired_observation_and_late_result_cannot_restore_old_label(self):
        frame = np.random.default_rng(4).integers(0, 256, (240, 320, 3), dtype=np.uint8)
        self.video.crop_worker.submit(1, frame, (20, 20, 150, 190))
        state = self.video.crop_worker._observations[1]
        from datetime import datetime, timezone
        observation = Observation(state.task_id, 1, self.video.execution_id, 0, 1, datetime.now(timezone.utc))
        result = MatchResult(observation, 12, 'new', None)
        self.video.osnet_worker = SimpleNamespace(accepting=True, take_results=lambda: [result],
                                                 end_person=MagicMock(), reset=MagicMock())
        self.video._poll_reid_results()
        self.assertIn('Global:12', self.labels()[0])
        self.video.state_manager.remove_disappeared_persons = lambda: [1]
        self.video._expire_persons()
        self.video.crop_worker.submit(1, frame, (20, 20, 150, 190))
        self.video._poll_reid_results()  # 이전 UUID의 늦은 결과
        self.assertIn('Global:--', self.labels()[0])
        self.video.global_person_ids[1] = 22
        self.video.tracker = SimpleNamespace(tracking_generation=1)
        self.video._collect_crops(frame, [])
        self.assertIn('Global:--', self.labels()[0])


class LiveLabelPipelineTests(unittest.TestCase):
    def test_video_frames_change_from_unassigned_to_committed_global_id(self):
        api, extractor = Api(), Extractor()
        pipeline = ReIDPipeline('http://test', 'token', lambda: extractor, lambda *args: api)
        video = VideoWorker(camera_id=1, reid_pipeline=pipeline)
        frame = np.random.default_rng(5).integers(0, 256, (240, 320, 3), dtype=np.uint8)
        deadline = time.monotonic() + 5
        class Stream:
            is_rtsp = False
            def open(self): return True
            def read(self):
                time.sleep(.01)
                return (True, frame.copy()) if time.monotonic() < deadline else (False, None)
            def pop_connection_events(self): return []
            def release(self): return True
        video.stream = Stream()
        labels, logs = [], []
        original = cv2.putText
        def draw(image, label, *args):
            labels.append(label)
            return original(image, label, *args)
        video.frame_ready.connect(lambda image: video.request_stop() if video.global_person_ids else None)
        tracker = SimpleNamespace(tracking_generation=0,
            track=lambda image: [dict(person_id=1, bbox=(20, 20, 150, 190), conf=.97)],
            expire_persons=lambda people: None)
        with TemporaryDirectory() as tmp:
            video.crop_worker.collector.output_root = Path(tmp)
            video.crop_worker.sample_interval = 0
            try:
                with patch('workers.video_worker.PersonTracker', return_value=tracker), \
                        patch('workers.video_worker.cv2.putText', side_effect=draw), \
                        patch('builtins.print', side_effect=lambda *args, **kwargs: logs.append(' '.join(map(str, args)))):
                    video.run()
                self.assertIn('Local:1 Global:--', labels)
                self.assertIn('Local:1 Global:1', labels)
                self.assertIn('FULL Det:0.97', labels)
                self.assertTrue(any('[ReID]' in line and 'elapsed_ms=' in line for line in logs))
                self.assertEqual(len(api.rows), 1)
                self.assertTrue(video.cleanup_ok)
                self.assertEqual(video.global_person_ids, {})
            finally:
                video.request_stop()
                pipeline.request_stop()
                pipeline.join(5)
                self.assertFalse(pipeline.is_alive())
                video.deleteLater()
