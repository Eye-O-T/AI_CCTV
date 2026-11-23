"""저장소 루트: python -m client_code.appearance 이미지경로 --device cpu"""
import argparse
import json

from .analyzer import AppearanceAnalyzer


def main():
    parser = argparse.ArgumentParser(description='대표 crop 한 장의 인상착의 JSON 출력')
    parser.add_argument('image_path')
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda', 'cuda:0'), default='auto')
    args = parser.parse_args()
    try:
        result = AppearanceAnalyzer(device=args.device).analyze(args.image_path)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'CNN 분석 실패: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
