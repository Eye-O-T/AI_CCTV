"""실제 JPEG 저장과 합성 입력 VideoWorker 연결 검증. 카메라·운영 DB는 사용하지 않는다."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import test_camera_runtime  # 기존 offscreen Qt 환경과 client_code import 경로 재사용
from storage.crop_collector import CropCollector


class CropCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.collector = CropCollector(1, output_root=Path(self.temp.name) / '한글 output')
        self.frame = np.random.default_rng(10).integers(0, 256, (240, 320, 3), dtype=np.uint8)
        self.bbox = (20, 20, 150, 190)
        self.clock = 0.0
        clock_patch = patch('storage.crop_collector.time', SimpleNamespace(monotonic=lambda: self.clock))
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def collect(self, at, frame=None, bbox=None):
        self.clock = at
        return self.collector.collect(1, self.frame if frame is None else frame,
                                      self.bbox if bbox is None else bbox)

    def test_interval_five_readable_jpegs_and_no_duplicate_save(self):
        self.collect(0)
        self.collect(.49)
        self.assertEqual(len(self.collector.collections[1].images), 1)
        for at in (.5, 1, 1.5):
            self.assertFalse(self.collect(at))
        self.assertFalse(self.collector.output_root.exists())
        paths = self.collect(2)
        self.assertEqual([p.name for p in paths], [f'crop_{i:02d}.jpg' for i in range(1, 6)])
        for path in paths:
            decoded = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(decoded.shape, (170, 130, 3))
        self.assertEqual(paths[0].parent.parent.parent.name, 'camera_1')
        self.assertFalse(self.collect(3))
        self.assertEqual(len(list(self.collector.output_root.rglob('*.jpg'))), 5)
        self.assertFalse(self.collector.collections[1].images)

    def test_blur_small_empty_and_outside_boxes_do_not_count(self):
        self.collect(0, frame=np.zeros_like(self.frame))
        self.collect(.5, bbox=(10, 10, 20, 30))
        self.collect(1, bbox=(50, 50, 20, 20))
        self.collect(1.5, bbox=(400, 20, 600, 200))
        self.assertEqual(self.collector.collections[1].images, [])
        self.assertFalse(self.collector.output_root.exists())
        self.collect(2, bbox=(-10, -20, 150, 190))
        self.assertEqual(self.collector.collections[1].images[0].shape, (190, 150, 3))

    def test_candidates_own_pixels_and_partial_collection_is_discarded(self):
        expected = self.frame[20:190, 20:150].copy()
        self.collect(0)
        self.frame[:] = 0
        np.testing.assert_array_equal(self.collector.collections[1].images[0], expected)
        self.collector.remove_person(1)
        self.assertFalse(self.collector.collections)
        self.assertFalse(self.collector.output_root.exists())

    def test_failed_save_is_not_complete_and_does_not_retry_each_frame(self):
        for at in (0, .5, 1, 1.5):
            self.collect(at)
        real_encode = cv2.imencode
        calls = 0

        def encode(*args):
            nonlocal calls
            calls += 1
            return real_encode(*args) if calls == 1 else (False, None)

        with patch('storage.crop_collector.cv2.imencode', side_effect=encode):
            with self.assertRaises(OSError):
                self.collect(2)
            self.assertFalse(self.collect(3))
        state = self.collector.collections[1]
        self.assertTrue(state.failed)
        self.assertFalse(state.saved_paths)
        self.assertFalse(state.images)
        self.assertEqual(list(self.collector.output_root.rglob('*.jpg')), [])
        self.assertEqual(list(self.collector.output_root.rglob('*.pending')), [])

    def test_existing_folder_is_preserved_on_same_id_and_time(self):
        frozen = datetime(2026, 11, 15, 14, 30, 25, 123000)
        with patch('storage.crop_collector.datetime') as wall_clock:
            wall_clock.now.return_value = frozen
            for at in (0, .5, 1, 1.5, 2):
                first = self.collect(at)
            before = first[0].read_bytes()
            self.collector.clear()
            for at in (3, 3.5, 4, 4.5, 5):
                second = self.collect(at)
        self.assertNotEqual(first[0].parent, second[0].parent)
        self.assertEqual(first[0].read_bytes(), before)
        self.assertEqual(len(list(self.collector.output_root.rglob('*.jpg'))), 10)

if __name__ == '__main__':
    unittest.main()
