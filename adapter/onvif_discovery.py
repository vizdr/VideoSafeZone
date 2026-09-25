"""WS-Discovery scan + ONVIF enrichment, shared by discover-onvif.py (CLI) and the local
ONVIF admin GUI (adapter/onvif-admin/app.py). See discover-onvif.py's module docstring
for how the protocol itself works -- this module is just the two stages factored out so
they're not duplicated between the CLI tool and the web app.
"""
import asyncio
import os
import sys

from onvif import ONVIFCamera
from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
from wsdiscovery import QName

# $VMS_HOME if set, otherwise the repo root (this file lives in adapter/).
VMS_HOME = os.environ.get("VMS_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WSDL_DIR = os.path.join(VMS_HOME, "venv-adapter", "lib",
    f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages", "onvif", "wsdl")
NVT_TYPE = QName("http://www.onvif.org/ver10/network/wsdl", "NetworkVideoTransmitter")


def scan(timeout: int = 4):
    """Anonymous multicast probe. Returns wsdiscovery Service objects -- XAddrs + Scopes
    only, no stream URL and no auth, since discovery itself carries no credentials."""
    wsd = WSDiscovery()
    wsd.start()
    try:
        return wsd.searchServices(types=[NVT_TYPE], timeout=timeout)
    finally:
        wsd.stop()


def xaddr_host_port(xaddr: str) -> tuple[str, int]:
    """'http://192.168.178.67:8080/onvif/device_service' -> ('192.168.178.67', 8080)."""
    addr_port = xaddr.split("://")[1].split("/")[0]
    host, _, port = addr_port.partition(":")
    return host, int(port) if port else 80


def locate(timeout: int = 4) -> dict[str, tuple[str, int]]:
    """Scan, then map each device's WS-Discovery endpoint reference to its current
    (host, port). The endpoint reference (`urn:uuid:...`) is the device's own stable
    identity -- often derived from its MAC -- and unlike the address it survives a DHCP
    lease change, which is what rematch_cameras.py relies on. A device announcing several
    XAddrs (IPv6, second NIC) is reported at its first IPv4 one."""
    found = {}
    for svc in scan(timeout):
        epr, xaddrs = svc.getEPR(), svc.getXAddrs()
        if not epr or not xaddrs:
            continue
        ipv4 = [x for x in xaddrs if xaddr_host_port(x)[0].replace(".", "").isdigit()]
        found[epr] = xaddr_host_port((ipv4 or xaddrs)[0])
    return found


async def enrich(xaddr: str, user: str, password: str) -> dict:
    """Authenticated second stage: turns a bare XAddrs into device info + a real RTSP
    stream URI, and reports whether the device exposes IR-cut control (so the caller can
    set hasIrControl without a second round trip)."""
    host, port = xaddr_host_port(xaddr)

    cam = ONVIFCamera(host, port, user, password, wsdl_dir=WSDL_DIR)
    await cam.update_xaddrs()

    devicemgmt = await cam.create_devicemgmt_service()
    info = await devicemgmt.GetDeviceInformation()

    media = await cam.create_media_service()
    profiles = await media.GetProfiles()
    stream_uri = None
    video_source_token = None
    if profiles:
        video_source_token = profiles[0].VideoSourceConfiguration.SourceToken
        uri_resp = await media.GetStreamUri({
            "StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
            "ProfileToken": profiles[0].token,
        })
        stream_uri = uri_resp.Uri

    has_ir_control = False
    if video_source_token:
        try:
            imaging = await cam.create_imaging_service()
            options = await imaging.GetOptions({"VideoSourceToken": video_source_token})
            has_ir_control = bool(options.IrCutFilterModes)
        except Exception:
            has_ir_control = False

    await cam.close()
    return {
        "host": host,
        "port": port,
        "manufacturer": info.Manufacturer,
        "model": info.Model,
        "firmware": info.FirmwareVersion,
        "profile": profiles[0].Name if profiles else None,
        "stream_uri": stream_uri,
        "has_ir_control": has_ir_control,
    }


def enrich_sync(xaddr: str, user: str, password: str) -> dict:
    return asyncio.run(enrich(xaddr, user, password))
