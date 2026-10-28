"""The world the admin GUI's tests start from (PYTEST.md, Phase 3): the app itself, the registry
rows, and the cameras. The fixtures that install it are in tests/admin/conftest.py; this module
holds what test files import, since a conftest can't be imported by name."""
import onvif_media2
from fakes import FakeMedia2, encoder_options, encoder_xml
from loaders import load_admin

admin = load_admin()
admin.app.testing = True          # an unhandled exception fails the test instead of becoming a 500

UNREG = "2026-10-25T12:00:00Z"


def arn(stream):
    return f"arn:aws:kinesisvideo:eu-central-1:000000000000:stream/{stream}/1700000000000"


# The registry as a test starts with it: the USB camera, the two older ONVIF cameras, a
# GUI-registered camera recording main with live view on sub, and an unregistered one.
CAM01 = {"cameraId": "cam-01", "mode": "transcode", "kvsStreamArn": arn("cam-01"),
         "videoCodecCaps": {"h264": "pi-hw", "h265": "none"}, "videoCodecDefault": "h264"}
CAM02 = {"cameraId": "cam-02", "mode": "passthrough", "onvifHost": "192.0.2.12", "onvifPort": 80,
         "onvifUser": "admin", "onvifPassword": "pw2", "hasIrControl": True, "audioCapable": True,
         "rtspUrl": "rtsp://192.0.2.12:554/Streaming/Channels/101?username=admin&password=5f4dcc3b",
         "kvsStreamArn": arn("cam-02")}
CAM03 = {"cameraId": "cam-03", "mode": "passthrough", "onvifHost": "192.0.2.13", "onvifPort": 80,
         "onvifUser": "admin", "onvifPassword": "pw3", "rtspUrl": "rtsp://admin:pw3@192.0.2.13:554/main",
         "liveRtspUrl": "rtsp://admin:pw3@192.0.2.13:554/sub", "kvsStreamArn": arn("cam-03"),
         "recordingMode": "manual", "outageBufferSec": 300, "detectionModes": ["motion"],
         "videoCodec": "h265", "videoCodecCaps": {"h264": "camera", "h265": "camera"},
         "displayName": "Hall"}
CAM05 = {"cameraId": "cam-05", "mode": "passthrough", "onvifHost": "192.0.2.15", "onvifPort": 80,
         "onvifUser": "admin", "onvifPassword": "pw5", "rtspUrl": "rtsp://admin:pw5@192.0.2.15:554/main",
         "kvsStreamArn": arn("cam-05"), "unregisteredAt": UNREG, "displayName": "Garage"}
ROWS = [CAM01, CAM02, CAM03, CAM05]
PRODUCERS = ("cam-01", "cam-02", "cam-03", "cam-05")


def media2(host, main="H265", main_size=(2560, 1440), sub=True, main_fps="15", options=None,
           on_set=None):
    """An ONVIF camera at `host` with a main stream (encoder E0) and, optionally, a sub stream (E1)."""
    streams = [("PROFILE_000", "E0", "/main", encoder_xml(main, *main_size, fps=main_fps, gov="30"))]
    if sub:
        streams.append(("PROFILE_001", "E1", "/sub", encoder_xml("H264", 800, 448, fps="15", gov="30")))
    return FakeMedia2(host, streams, options or {"E0": encoder_options("H264", "H265"),
                                                 "E1": encoder_options("H264", "H265")}, on_set)


class Unreachable:
    """A camera that doesn't answer: every Media2 call raises, as onvif_media2 does."""

    def __getattr__(self, name):
        def call(*a, **k):
            raise onvif_media2.OnvifError("192.0.2.99 unreachable (ConnectTimeout)")
        return call


def no_detection(*a, **k):
    raise OSError("camera unreachable")


def systemctl_writes(system):
    """The commands that change something: start/stop/enable/disable, provisioning."""
    return [c for c in system.calls if c[0] == "sudo"]
