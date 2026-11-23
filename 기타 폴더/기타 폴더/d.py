import cv2

backends = {
    "DirectShow": cv2.CAP_DSHOW,
    "Media Foundation": cv2.CAP_MSMF
}

for backend_name, backend in backends.items():
    print(f"\n=== {backend_name} ===")

    for index in range(5):
        print(f"Testing camera {index}...", flush=True)

        cap = cv2.VideoCapture(index, backend)

        if cap.isOpened():
            ret, frame = cap.read()

            if ret:
                print(f"Camera {index}: SUCCESS")

                cv2.imshow(
                    f"{backend_name} - Camera {index}",
                    frame
                )
                cv2.waitKey(2000)
                cv2.destroyAllWindows()
            else:
                print(f"Camera {index}: Frame failed")
        else:
            print(f"Camera {index}: Open failed")

        cap.release()

cv2.destroyAllWindows()