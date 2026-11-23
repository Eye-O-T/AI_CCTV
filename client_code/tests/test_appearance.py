"""나이 후처리·다중 라벨 선택·고정 가중치 단독 추론 검증."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
import torch

from client_code.appearance.analyzer import (
    AppearanceAnalyzer, DEFAULT_WEIGHTS_PATH, age_group, decode_predictions,
)


class PostprocessTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {
            'model_version': 'exp3',
            'attributes': {
                'gender': ['Male', 'Female'],
                'age': ['<=15', '16-30', '31-45', '46-60', '61+'],
                'upper_color': ['Black', 'Blue', 'White'],
                'lower_color': ['Black', 'Blue', 'White'],
                'carrying': ['Backpack', 'Other'],
            },
            'multi_label': ['upper_color', 'lower_color', 'carrying'],
            'display_rules': {
                'upper_color': {'always_top': True, 'threshold': .7},
                'lower_color': {'always_top': True, 'threshold': None},
                'carrying': {'always_top': False, 'threshold': .6},
            },
        }
        self.logits = {name: torch.full((1, len(labels)), -4.)
                       for name, labels in self.bundle['attributes'].items()}
        self.logits['gender'][0, 0] = 4
        self.logits['age'][0, 1] = 4

    def test_all_age_classes_and_genders(self):
        for gender, youngest in [('Male', '소년'), ('Female', '소녀')]:
            for age, expected in [('<=15', youngest), ('16-30', '청년'),
                                  ('31-45', '청년'), ('46-60', '장년'), ('61+', '장년')]:
                with self.subTest(gender=gender, age=age):
                    self.assertEqual(age_group(age, gender), expected)

    def test_age_uses_winning_original_class_not_grouped_probabilities(self):
        # 청년 두 클래스의 합이 커도, 원래 argmax인 46-60을 장년으로 변환한다.
        self.logits['age'] = torch.tensor([[-8., 2., 2., 2.1, -8.]])
        self.assertEqual(decode_predictions(self.logits, self.bundle)['attributes']['age'], '장년')

    def test_multilabel_thresholds_and_no_carrying(self):
        self.logits['upper_color'] = torch.tensor([[2., -4., 1.]])
        self.logits['lower_color'] = torch.tensor([[2., 3., 1.]])
        result = decode_predictions(self.logits, self.bundle)
        self.assertEqual(result['attributes']['upper_color'], ['Black', 'White'])
        self.assertEqual(result['attributes']['lower_color'], ['Blue'])
        self.assertEqual(result['attributes']['carrying'], [])
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)

    def test_upper_color_always_has_top_and_carrying_can_have_multiple(self):
        self.logits['upper_color'] = torch.tensor([[-3., -1., -2.]])
        self.logits['carrying'] = torch.tensor([[2., 1.]])
        attrs = decode_predictions(self.logits, self.bundle)['attributes']
        self.assertEqual(attrs['upper_color'], ['Blue'])
        self.assertEqual(attrs['carrying'], ['Backpack', 'Other'])

    def test_invalid_logits_rejected(self):
        for invalid in (torch.zeros(2, 2), torch.zeros(1, 3),
                        torch.tensor([[float('nan'), 0.]]),
                        torch.tensor([[float('inf'), 0.]])):
            with self.subTest(shape=invalid.shape):
                self.logits['gender'] = invalid
                with self.assertRaises(ValueError):
                    decode_predictions(self.logits, self.bundle)


class AnalyzerFailureTests(unittest.TestCase):
    def test_missing_or_changed_weights_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'par_serving_exp3.pt'
            with self.assertRaises(FileNotFoundError):
                AppearanceAnalyzer(path, device='cpu')
            path.write_bytes(b'not the original model')
            with self.assertRaisesRegex(ValueError, '원본'):
                AppearanceAnalyzer(path, device='cpu')


@unittest.skipUnless(DEFAULT_WEIGHTS_PATH.is_file(), '로컬 exp3 가중치 필요')
class RealModelTests(unittest.TestCase):
    def test_cpu_inference_and_image_failure(self):
        analyzer = AppearanceAnalyzer(device='cpu')
        self.assertFalse(analyzer._model.training)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / '대표 이미지.png'
            Image.new('L', (80, 160), 120).save(path)
            result = analyzer.analyze(path)
            self.assertEqual(result['model_version'], 'exp3')
            self.assertEqual(len(result['attributes']), 8)
            self.assertIn(result['attributes']['age'], ['소년', '소녀', '청년', '장년'])
            json.dumps(result, allow_nan=False)
            with self.assertRaises(FileNotFoundError):
                analyzer.analyze(Path(tmp) / 'missing.jpg')
            path.write_bytes(b'not an image')
            with self.assertRaises(OSError):
                analyzer.analyze(path)


if __name__ == '__main__':
    unittest.main()
