"""임시 녹화→완료 파일 이동, 날짜/카메라 분리 및 실패 시 원본 보존."""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage.recording_manager import RecordingManager


class RecordingLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / '사용자 저장소' / 'AI_CCTV'
        self.staging = self.root / '프로젝트' / 'temp'
        self.frame = np.full((48, 64, 3), 90, np.uint8)

    def manager(self, camera_id=7, **kwargs):
        return RecordingManager(self.destination, camera_id=camera_id,
                                temp_root=self.staging, fps=10, **kwargs)

    def assert_playable(self, path):
        capture = cv2.VideoCapture(str(path))
        try:
            ok, frame = capture.read()
            self.assertTrue(ok, str(path))
            self.assertEqual(frame.shape, self.frame.shape)
        finally:
            capture.release()

    def test_only_closed_recording_is_moved_and_empty_staging_leaf_removed(self):
        manager = self.manager()
        self.addCleanup(manager.stop_recording)
        manager.write_frame(self.frame)
        source = Path(manager.temp_save_path)
        self.assertTrue(source.is_relative_to(self.staging))
        self.assertTrue(source.exists())
        self.assertFalse(list(self.destination.rglob('*.mp4')))
        real_rename = __import__('os').rename

        def move_after_release(source_path, destination_path):
            self.assertIsNone(manager.writer)
            real_rename(source_path, destination_path)

        with patch('storage.recording_manager.os.rename', side_effect=move_after_release):
            manager.stop_recording()
        self.assertFalse(source.exists())
        self.assertFalse(source.parent.exists())
        videos = list(self.destination.glob('원본 녹화본/*/camera_7/*.mp4'))
        self.assertEqual(len(videos), 1)
        self.assert_playable(videos[0])

    def test_midnight_rotation_uses_each_segment_start_date(self):
        manager = self.manager(segment_seconds=10)
        self.addCleanup(manager.stop_recording)
        started = datetime(2026, 11, 19, 23, 59, 55)
        with patch('storage.recording_manager.datetime') as clock:
            clock.now.return_value = started
            manager.write_frame(self.frame)
            clock.now.return_value = started + timedelta(seconds=10)
            manager.write_frame(self.frame)
            first = self.destination / '원본 녹화본/2026-11-19/camera_7/23시59분55초~2026-11-20_00시00분05초.mp4'
            self.assertTrue(first.exists())
            self.assertTrue(Path(manager.temp_save_path).exists())
            clock.now.return_value += timedelta(seconds=1)
            manager.stop_recording()
        second = self.destination / '원본 녹화본/2026-11-20/camera_7/00시00분05초~00시00분06초.mp4'
        self.assertTrue(second.exists())
        self.assert_playable(first)
        self.assert_playable(second)

    def test_same_time_and_multiple_cameras_never_overwrite(self):
        started = datetime(2026, 11, 19, 14, 0, 0)
        with patch('storage.recording_manager.datetime') as clock:
            clock.now.return_value = started
            # 동시 시작으로 임시 경로가 겹쳐도 각각 보존한다.
            first, second, other = self.manager(), self.manager(), self.manager(8)
            for manager in (first, second, other):
                self.addCleanup(manager.stop_recording)
                manager.write_frame(self.frame)
            self.assertNotEqual(first.temp_save_path, second.temp_save_path)
            first.stop_recording()
            original = next(self.destination.glob('원본 녹화본/*/camera_7/*.mp4'))
            before = original.read_bytes()
            second.stop_recording()
            other.stop_recording()
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(len(list(self.destination.glob('원본 녹화본/*/camera_7/*.mp4'))), 2)
        self.assertEqual(len(list(self.destination.glob('원본 녹화본/*/camera_8/*.mp4'))), 1)

    def test_move_failure_keeps_playable_source_and_blocks_reuse(self):
        manager = self.manager()
        manager.write_frame(self.frame)
        source = Path(manager.temp_save_path)
        with patch('storage.recording_manager.os.rename', side_effect=PermissionError):
            with self.assertRaises(OSError):
                manager.stop_recording()
        self.assertIsNone(manager.writer)
        self.assertTrue(source.exists())
        self.assertFalse(list(self.destination.rglob('*.mp4')))
        self.assert_playable(source)
        with self.assertRaises(OSError):
            manager.write_frame(self.frame)
        with self.assertRaises(OSError):
            manager.stop_recording()
        self.assertEqual(Path(manager.temp_save_path), source)

    def test_destination_creation_failure_preserves_source(self):
        manager = self.manager()
        manager.write_frame(self.frame)
        source = Path(manager.temp_save_path)
        with patch('storage.recording_manager.Path.mkdir', side_effect=PermissionError):
            with self.assertRaises(OSError):
                manager.stop_recording()
        self.assertTrue(source.exists())
        self.assert_playable(source)

    def test_settings_creates_original_and_event_directories(self):
        from test_client_auth import APP
        from ui.settings_window import SettingsWindow
        window = SettingsWindow()
        try:
            with patch('ui.settings_window.QFileDialog.getExistingDirectory', return_value=str(self.root)):
                window.select_storage_path()
            destination = self.root / 'AI_CCTV'
            self.assertEqual(Path(window.ai_cctv_path), destination)
            self.assertTrue((destination / '원본 녹화본').is_dir())
            self.assertTrue((destination / '이벤트').is_dir())
            self.assertFalse((destination / '이벤트 CLIP').exists())
        finally:
            window.close()
            window.deleteLater()
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
