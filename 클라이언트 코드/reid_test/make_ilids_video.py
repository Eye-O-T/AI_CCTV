import cv2
import numpy as np
from pathlib import Path


def imread_unicode(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return image


def images_to_video(image_folder, output_file, fps=10):

    image_folder = Path(image_folder)

    images = []

    for ext in ["*.png", "*.jpg", "*.jpeg", "*.bmp"]:
        images.extend(image_folder.glob(ext))

    images = sorted(images)

    if not images:
        raise RuntimeError(
            f"이미지를 찾을 수 없습니다: {image_folder}"
        )

    print(image_folder)
    print(f"이미지 개수: {len(images)}")

    # 첫 이미지
    first_frame = imread_unicode(images[0])

    if first_frame is None:
        raise RuntimeError("첫 이미지를 읽을 수 없습니다.")

    height, width = first_frame.shape[:2]

    writer = cv2.VideoWriter(
        output_file,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height)
    )

    for image_path in images:

        frame = imread_unicode(image_path)

        if frame is None:
            print(f"읽기 실패: {image_path}")
            continue

        frame = cv2.resize(
            frame,
            (width, height)
        )

        writer.write(frame)

    writer.release()

    print(f"완료: {output_file}")
    print()


CAM1_FOLDER = r"C:\Github\AI_CCTV\클라이언트 코드\reid_test\iLIDS-VID\i-LIDS-VID\sequences\cam1\person062"

CAM2_FOLDER = r"C:\Github\AI_CCTV\클라이언트 코드\reid_test\iLIDS-VID\i-LIDS-VID\sequences\cam2\person062"


images_to_video(
    CAM1_FOLDER,
    "video1.mp4"
)

images_to_video(
    CAM2_FOLDER,
    "video2.mp4"
)

print("두 영상 생성 완료")