"""복구 저장 1~2단계: 임시 ZIP/합성 TS만 사용하며 운영 미디어에 접근하지 않는다."""
import io
import sys
import unittest
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recovery.network_recovery_manager import NetworkRecoveryManager


class RecoveryLayoutTests(unittest.TestCase):
    @staticmethod
    def fake_merge(files, output, work):
        Path(output).write_bytes(b'fake MP4')
        return {'success': True}

    @staticmethod
    def response(contents=None):
        response = MagicMock(status_code=200, ok=True, headers={})
        if contents is None:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('backup_00001.ts', b'fake TS')
            contents = buffer.getvalue()
        response.iter_content.return_value = [contents]
        return response

    @staticmethod
    def payload(manager):
        return manager.build_payload(datetime(2026, 11, 21, 14, 0, 10),
                                     datetime(2026, 11, 21, 14, 0, 25))

    def test_success_publishes_then_cleans_only_own_job(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            previous = Path(temporary) / 'temp/recovery/camera_3/recovery_previous'
            previous.mkdir(parents=True)
            (previous / 'keep.zip').write_bytes(b'old failure')
            response = self.response()
            def merge(files, output, work):
                self.assertEqual(Path(output).parent, Path(work))
                self.assertTrue(Path(work).is_relative_to(previous.parent))
                self.assertEqual(list((Path(temporary) / '원본 녹화본').rglob('*.mp4')), [])
                return self.fake_merge(files, output, work)
            with patch('requests.get', return_value=response), patch.object(manager, '_merge_ts_files', side_effect=merge):
                result = manager.request_recovery(self.payload(manager))
            self.assertTrue(result['success'])
            self.assertEqual(Path(result['file_path']).read_bytes(), b'fake MP4')
            self.assertIsNone(result['zip_path'])
            self.assertEqual(list(previous.parent.iterdir()), [previous])
            self.assertEqual((previous / 'keep.zip').read_bytes(), b'old failure')
            response.close.assert_called_once()

    def test_partial_download_preserved_without_final_mp4(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            response = self.response()
            def interrupted():
                yield b'partial ZIP'
                raise OSError('connection lost')
            response.iter_content.return_value = interrupted()
            result = manager._process_recovery_response(response, self.payload(manager))
            self.assertFalse(result['success'])
            self.assertEqual(len(list(Path(temporary).rglob('*.zip'))), 1)
            self.assertEqual(list(Path(temporary).rglob('*.mp4')), [])

    def test_bad_zip_and_empty_ts_preserved(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            empty = io.BytesIO()
            with zipfile.ZipFile(empty, 'w'):
                pass
            for contents in (b'not a ZIP', empty.getvalue()):
                result = manager._process_recovery_response(self.response(contents), self.payload(manager))
                self.assertFalse(result['success'])
                self.assertTrue(Path(result['zip_path']).exists())
            self.assertEqual(list(Path(temporary).rglob('*.mp4')), [])

    def test_conversion_failure_preserves_zip_ts_and_partial_mp4(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            def failed_merge(files, output, work):
                Path(output).write_bytes(b'partial MP4')
                return {'success': False, 'error': 'conversion failed'}
            with patch.object(manager, '_merge_ts_files', side_effect=failed_merge):
                result = manager._process_recovery_response(self.response(), self.payload(manager))
            self.assertFalse(result['success'])
            work = Path(result['zip_path']).parent
            self.assertTrue((work / 'merged.mp4').exists())
            self.assertEqual(len(list((work / 'ts').rglob('*.ts'))), 1)
            self.assertEqual(list((Path(temporary) / '원본 녹화본').rglob('*.mp4')), [])

    def test_move_failure_preserves_complete_temporary_mp4(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            with patch.object(manager, '_merge_ts_files', side_effect=self.fake_merge), \
                 patch('recovery.network_recovery_manager.os.rename', side_effect=PermissionError), \
                 patch('recovery.network_recovery_manager.os.link', side_effect=PermissionError):
                result = manager._process_recovery_response(self.response(), self.payload(manager))
            self.assertFalse(result['success'])
            self.assertEqual((Path(result['zip_path']).parent / 'merged.mp4').read_bytes(), b'fake MP4')
            self.assertEqual(list((Path(temporary) / '원본 녹화본').rglob('*.mp4')), [])

    def test_empty_conversion_result_is_not_published(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            def empty_merge(files, output, work):
                Path(output).touch()
                return {'success': True}
            with patch.object(manager, '_merge_ts_files', side_effect=empty_merge):
                result = manager._process_recovery_response(self.response(), self.payload(manager))
            self.assertFalse(result['success'])
            self.assertTrue(Path(result['zip_path']).exists())
            self.assertEqual(list((Path(temporary) / '원본 녹화본').rglob('*.mp4')), [])

    def test_cleanup_failure_reports_warning_but_keeps_saved_success(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            with patch.object(manager, '_merge_ts_files', side_effect=self.fake_merge), \
                 patch('recovery.network_recovery_manager.shutil.rmtree', side_effect=PermissionError):
                result = manager._process_recovery_response(self.response(), self.payload(manager))
            self.assertTrue(result['success'])
            self.assertIn('cleanup_warning', result)
            self.assertTrue(Path(result['file_path']).exists())
            self.assertTrue(Path(result['zip_path']).exists())

    def test_parallel_jobs_and_cameras_do_not_overwrite_or_delete_each_other(self):
        with TemporaryDirectory() as temporary:
            managers = [NetworkRecoveryManager(camera_id=f'camera_{camera}', base_dir=temporary)
                        for camera in (3, 3, 4)]
            def recover(manager):
                return manager._process_recovery_response(self.response(), self.payload(manager))
            with patch.object(NetworkRecoveryManager, '_merge_ts_files', side_effect=self.fake_merge), \
                 ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(recover, managers))
            self.assertTrue(all(result['success'] for result in results))
            self.assertEqual(len({result['file_path'] for result in results}), 3)
            self.assertTrue(all(Path(result['file_path']).read_bytes() == b'fake MP4' for result in results))
            self.assertEqual(list((Path(temporary) / 'temp/recovery').rglob('recovery_*')), [])

    def test_same_original_folder_and_chronological_names(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for camera in (3, 4):
                manager = NetworkRecoveryManager(camera_id=f'camera_{camera}', base_dir=temporary)
                payload = manager.build_payload(datetime(2026, 11, 21, 14, 0, 10),
                                                datetime(2026, 11, 21, 14, 0, 25))
                zipped = root / f'camera_{camera}.zip'
                with zipfile.ZipFile(zipped, 'w') as archive:
                    archive.writestr('backup_00001.ts', b'fake TS')
                with patch.object(manager, '_merge_ts_files', side_effect=self.fake_merge):
                    result = manager._extract_and_merge(str(zipped), payload)
                path = Path(result['file_path'])
                self.assertEqual(path.parent, root / '원본 녹화본' / '2026-11-21' / f'camera_{camera}')
                self.assertEqual(path.name, '14시00분10초~14시00분25초(장애복구파일).mp4')
                before = '14시00분00초~14시00분10초.mp4'
                after = '14시00분25초~14시00분35초.mp4'
                self.assertEqual(sorted([after, path.name, before]), [before, path.name, after])
                self.assertTrue(zipped.exists())

    def test_midnight_uses_start_date_and_dated_end(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(camera_id='camera_3', base_dir=temporary)
            # datetime 객체 없이 ISO 시각 문자열만 전달해도 같은 규칙을 사용한다.
            payload = {'start': '2026-11-21T23:59:58', 'end': '2026-11-22T00:00:12'}
            zipped = Path(temporary) / 'recovery.zip'
            with zipfile.ZipFile(zipped, 'w') as archive:
                archive.writestr('one.ts', b'fake TS')
            with patch.object(manager, '_merge_ts_files', side_effect=self.fake_merge):
                result = manager._extract_and_merge(str(zipped), payload)
            self.assertEqual(Path(result['file_path']).relative_to(temporary).as_posix(),
                             '원본 녹화본/2026-11-21/camera_3/23시59분58초~2026-11-22_00시00분12초(장애복구파일).mp4')

    def test_collision_keeps_existing_file_and_marker_at_end(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(base_dir=temporary)
            name = '14시00분10초~14시00분25초(장애복구파일).mp4'
            existing = Path(temporary) / name
            existing.write_bytes(b'keep original')
            second = Path(manager._get_unique_save_path(temporary, name))
            self.assertEqual(second.name, '14시00분10초~14시00분25초_2(장애복구파일).mp4')
            second.write_bytes(b'keep previous recovery')
            third = Path(manager._get_unique_save_path(temporary, name))
            self.assertEqual(third.name, '14시00분10초~14시00분25초_3(장애복구파일).mp4')
            self.assertEqual(existing.read_bytes(), b'keep original')
            self.assertEqual(second.read_bytes(), b'keep previous recovery')

    def test_ffmpeg_does_not_overwrite_file_created_after_path_check(self):
        with TemporaryDirectory() as temporary:
            manager = NetworkRecoveryManager(base_dir=temporary)
            target = Path(temporary) / 'existing.mp4'
            target.write_bytes(b'keep original')
            with patch('recovery.network_recovery_manager.subprocess.run') as run:
                run.return_value.returncode = 1
                run.return_value.stderr = 'File exists'
                result = manager._merge_ts_files(['one.ts'], str(target), temporary)
            self.assertIn('-n', run.call_args.args[0])
            self.assertNotIn('-y', run.call_args.args[0])
            self.assertFalse(result['success'])
            self.assertEqual(target.read_bytes(), b'keep original')


if __name__ == '__main__':
    unittest.main()
