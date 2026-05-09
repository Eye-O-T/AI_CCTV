import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")

from gi.repository import Gst, GstRtspServer, GLib


class CameraRTSPServer:
    def __init__(self):
        Gst.init(None)

        self.server = GstRtspServer.RTSPServer()
        self.server.set_service("8554")

        factory = GstRtspServer.RTSPMediaFactory()
        factory.set_shared(True)

        pipeline = (
            "( "
            "v4l2src device=/dev/video0 "
            "! video/x-raw,width=640,height=480,framerate=30/1 "
            "! videoconvert "
            "! x264enc tune=zerolatency speed-preset=ultrafast bitrate=1000 "
            "! rtph264pay config-interval=1 name=pay0 pt=96 "
            ")"
        )

        factory.set_launch(pipeline)

        mounts = self.server.get_mount_points()
        mounts.add_factory("/stream", factory)

        self.server.attach(None)

        print("RTSP 서버 시작")
        print("접속 주소: rtsp://192.168.10.2:8554/stream")


if __name__ == "__main__":
    server = CameraRTSPServer()
    loop = GLib.MainLoop()
    loop.run()