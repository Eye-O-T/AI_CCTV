"""사진 5장의 실제 추론 확인. 원본 이미지와 DB를 수정하지 않는다."""
import argparse
import json
import math
import time

from .osnet_extractor import OSNetError, OSNetExtractor


def main():
    parser = argparse.ArgumentParser(description='crop 5장으로 OSNet 대표 벡터 생성 확인')
    parser.add_argument('crops', nargs=5, help='서로 다른 저장 이미지 경로 5개')
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda:0'), default='auto')
    args = parser.parse_args()
    try:
        extractor = OSNetExtractor(device=args.device)
        started = time.perf_counter()
        vector = extractor.extract(args.crops)
    except OSNetError as error:
        parser.exit(1, f'{error.code}: {error}\n')
    print(json.dumps({'device': str(extractor.device), 'dimension': len(vector),
                      'norm': math.sqrt(sum(value * value for value in vector)),
                      'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}))


if __name__ == '__main__':
    main()
