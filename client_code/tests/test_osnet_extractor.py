"""실제 고정 가중치의 CPU 추론과 대표 벡터/오류/공유 잠금 검증."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import math
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import time
import unittest
from unittest.mock import patch

from PIL import Image
import torch

from client_code.feature_vector import decode_vector, encode_vector
from client_code.reid.config import DEFAULT_WEIGHTS_PATH
from client_code.reid.download_weights import download_weights
from client_code.reid.osnet_extractor import OSNetError, OSNetExtractor, representative_vector


class WeightDownloadTests(unittest.TestCase):
    def test_verified_download_published_and_reused(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / 'model.pth'
            with patch('client_code.reid.download_weights.WEIGHTS_SIZE', 4), \
                 patch('client_code.reid.download_weights.WEIGHTS_SHA256', hashlib.sha256(b'good').hexdigest()), \
                 patch('client_code.reid.download_weights.urlopen', return_value=io.BytesIO(b'good')) as request:
                self.assertEqual(download_weights(target), target)
                self.assertEqual(download_weights(target), target)
                self.assertEqual(request.call_count, 1)
            self.assertEqual(target.read_bytes(), b'good')
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)

    def test_corrupt_download_never_published(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / 'model.pth'
            with patch('client_code.reid.download_weights.urlopen', return_value=io.BytesIO(b'bad')):
                with self.assertRaises(RuntimeError):
                    download_weights(target)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_network_failure_leaves_no_partial_model(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / 'model.pth'
            with patch('client_code.reid.download_weights.urlopen', side_effect=OSError('network')):
                with self.assertRaises(OSError):
                    download_weights(target)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class RepresentativeVectorTests(unittest.TestCase):
    def test_each_image_normalized_before_average(self):
        features = torch.zeros((5, 512))
        features[:3, 0] = torch.tensor([1., 20., 300.])
        features[3:, 1] = torch.tensor([1., 2000.])
        result = representative_vector(features)
        self.assertAlmostEqual(result[0], 3 / math.sqrt(13), places=6)
        self.assertAlmostEqual(result[1], 2 / math.sqrt(13), places=6)
        self.assertEqual(result, decode_vector(encode_vector(result)))

    def test_invalid_features(self):
        values = [torch.ones((4, 512)), torch.ones((5, 513)), torch.zeros((5, 512)),
                  torch.full((5, 512), float('nan')), torch.full((5, 512), float('inf')),
                  torch.full((5, 512), torch.finfo(torch.float32).max), [1] * 512]
        for features in values:
            with self.subTest(kind=str(type(features))):
                with self.assertRaises(OSNetError) as error:
                    representative_vector(features)
                self.assertEqual(error.exception.code, 'INVALID_FEATURES')

    def test_device_selection_without_cuda(self):
        with patch('torch.cuda.is_available', return_value=False):
            self.assertEqual(str(OSNetExtractor._select_device('auto')), 'cpu')
            with self.assertRaises(OSNetError):
                OSNetExtractor._select_device('cuda:0')
        with self.assertRaises(OSNetError):
            OSNetExtractor._select_device('cuda:123')

    def test_current_pyqt_script_import_mode(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'from reid.osnet_extractor import OSNetExtractor; '
            'from reid.config import DEFAULT_WEIGHTS_PATH; '
            'assert DEFAULT_WEIGHTS_PATH.is_absolute(); print("import OK")'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('import OK', result.stdout)


class OSNetExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(2)  # 테스트 프로세스의 CPU 과다 스레딩만 제한한다.
        cls.addClassCleanup(torch.set_num_threads, cls.previous_threads)
        # 가중치 누락을 skip 성공으로 숨기지 않는다. README 준비 명령이 선행되어야 한다.
        cls.extractor = OSNetExtractor(device='cpu')

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / '한글 crop'
        self.folder.mkdir()
        self.paths = []
        for index in range(5):
            path = self.folder / f'crop_{index + 1:02d}.jpg'
            Image.new('RGB', (130 + index, 270), (40 + 10 * index, 100, 180)).save(path)
            self.paths.append(path)

    def test_actual_cpu_inference_repeatable_and_read_only(self):
        before = [path.read_bytes() for path in self.paths]
        result = self.extractor.extract(self.paths)
        repeated = self.extractor.extract(self.paths)
        self.assertEqual(len(result), 512)
        self.assertTrue(all(math.isfinite(value) for value in result))
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in result)), 1.0, places=6)
        self.assertEqual(result, repeated)
        self.assertEqual(before, [path.read_bytes() for path in self.paths])
        self.assertFalse(self.extractor._model.training)

    def test_rgb_preprocessing(self):
        transformed = self.extractor._preprocess(Image.new('RGB', (128, 256), (255, 0, 0)))
        self.assertEqual(tuple(transformed.shape), (3, 256, 128))
        for channel, expected in enumerate(((1 - .485) / .229, (0 - .456) / .224, (0 - .406) / .225)):
            self.assertAlmostEqual(transformed[channel, 0, 0].item(), expected, places=5)

    def test_wrong_count_and_duplicate_paths(self):
        for paths in (self.paths[:4], self.paths + self.paths[:1], [self.paths[0]] * 5, 'private-crop'):
            with self.assertRaises(OSNetError) as error:
                self.extractor.extract(paths)
            self.assertEqual(error.exception.code, 'INVALID_CROP_COUNT')
            self.assertNotIn('private-crop', str(error.exception))

    def test_missing_and_corrupt_image(self):
        for payload in (None, b'private-invalid-jpeg'):
            bad = self.folder / 'private-image.jpg'
            if payload is not None:
                bad.write_bytes(payload)
            with self.assertRaises(OSNetError) as error:
                self.extractor.extract([bad] + self.paths[1:])
            self.assertEqual(error.exception.code, 'IMAGE_READ_FAILED')
            self.assertNotIn('private', str(error.exception))

    def test_missing_or_wrong_weight_file_never_falls_back(self):
        bad = self.folder / 'private-model.pth'
        with self.assertRaises(OSNetError) as error:
            OSNetExtractor(bad, device='cpu')
        self.assertEqual(error.exception.code, 'WEIGHTS_NOT_FOUND')
        bad.write_bytes(b'private-model-content')
        with self.assertRaises(OSNetError) as error:
            OSNetExtractor(bad, device='cpu')
        self.assertEqual(error.exception.code, 'WEIGHTS_HASH_MISMATCH')
        self.assertNotIn('private', str(error.exception))
        with self.assertRaises(RuntimeError):
            download_weights(bad)
        self.assertEqual(bad.read_bytes(), b'private-model-content')

    def test_checkpoint_missing_backbone_and_classifier_exception(self):
        state = torch.load(DEFAULT_WEIGHTS_PATH, map_location='cpu', weights_only=True)
        state = state.get('state_dict', state)
        bad = self.folder / 'test.pth'
        missing = dict(state)
        missing.pop(next(key for key in missing if not key.startswith('classifier.')))
        torch.save(missing, bad)
        with patch('client_code.reid.osnet_extractor.weights_match', return_value=True):
            with self.assertRaises(OSNetError) as error:
                OSNetExtractor(bad, device='cpu')
            self.assertEqual(error.exception.code, 'MODEL_LOAD_FAILED')
        # 실제 학습 분류기 출력 수가 1과 달라도 특징 추출 계층 전체가 일치하면 로딩된다.
        self.assertNotEqual(state['classifier.weight'].shape[0], 1)

    def test_weight_downloader_reuses_verified_file(self):
        with patch('client_code.reid.download_weights.urlopen', side_effect=AssertionError('network used')):
            self.assertEqual(download_weights(), DEFAULT_WEIGHTS_PATH)

    def test_inference_failure_is_not_a_vector(self):
        with patch.object(self.extractor._model, 'forward', side_effect=RuntimeError('private-inference')):
            with self.assertRaises(OSNetError) as error:
                self.extractor.extract(self.paths)
            self.assertEqual(error.exception.code, 'INFERENCE_FAILED')
            self.assertNotIn('private', str(error.exception))

    def test_shared_extractor_serializes_forward(self):
        guard = Lock()
        active = 0
        peak = 0
        def forward(batch):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(active, peak)
            time.sleep(.03)
            with guard:
                active -= 1
            return torch.ones((5, 512))
        with patch.object(self.extractor._model, 'forward', side_effect=forward):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.extractor.extract(self.paths), range(2)))
        self.assertEqual(peak, 1)
        self.assertEqual(results[0], results[1])


if __name__ == '__main__':
    unittest.main()
