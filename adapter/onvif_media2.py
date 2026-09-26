"""ONVIF Media2 (ver20) -- just enough of it to read and switch a camera's video codec.

Why this is not the onvif library: H.265 is invisible to Media1 (ver10), whose encoding
enum stops at JPEG/MPEG4/H264, and the WSDL set bundled with python-onvif-zeep-async has
no ver20 media service at all. cam-02 advertises H265 only through Media2
(measurements/codec-phase0.md §2). Rather than vendoring the ver20 WSDL tree -- it imports
the ver10 schema by relative path -- this speaks the handful of SOAP calls needed directly,
with the same WS-Security UsernameToken digest the library uses.

Every write is verified by reading it back: this camera family has accepted ONVIF writes and
silently ignored them before (Camera-Features.md §4). Credentials never appear in return
values or exception text, and stream URIs are returned redacted.
"""
import base64
import datetime as dt
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import requests

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "tr2": "http://www.onvif.org/ver20/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"

# Project codec names <-> ONVIF Media2 encoding names.
TO_ONVIF = {"h264": "H264", "h265": "H265"}
FROM_ONVIF = {v: k for k, v in TO_ONVIF.items()}
H264_PROFILES = {"Baseline", "Main", "Extended", "High"}


class Media2Error(RuntimeError):
    pass


def redact_uri(uri: str) -> str:
    """Strip credentials from a stream URI: userinfo, and the ?username=&password= query
    this camera appends (with an unsalted MD5 of the password)."""
    uri = re.sub(r"//[^@/]*@", "//", uri or "")
    return re.sub(r"(password=)[^&]*", r"\1<redacted>", uri)


def stream_key(uri: str) -> tuple:
    """(port, path) -- how a registered rtspUrl is matched to a profile's stream URI.
    Host is left out on purpose: rematch_cameras.py follows a camera to a new address, and
    the query string carries credentials that differ between what was stored and what the
    camera reports."""
    u = urlparse(uri or "")
    return (u.port or 554, u.path.rstrip("/"))


def profile_for_stream(profiles: list, rtsp_url: str) -> dict | None:
    """The one profile whose stream URI is `rtsp_url`, or None -- never a guess when zero
    or several match."""
    key = stream_key(rtsp_url)
    hits = [p for p in profiles if stream_key(p.get("stream_uri_raw", "")) == key]
    return hits[0] if len(hits) == 1 else None


def _text(el, path):
    x = el.find(path, NS) if el is not None else None
    return x.text if x is not None else None


def encoder_summary(cfg) -> dict:
    """Flat view of a tr2:Configurations (VideoEncoder) element."""
    rc = cfg.find("tt:RateControl", NS)
    enc = _text(cfg, "tt:Encoding")
    return {
        "token": cfg.get("token"),
        "codec": FROM_ONVIF.get(enc, enc),
        "profile": cfg.get("Profile"),
        "gov": cfg.get("GovLength"),
        "width": int(_text(cfg, "tt:Resolution/tt:Width") or 0),
        "height": int(_text(cfg, "tt:Resolution/tt:Height") or 0),
        "fps": _text(cfg, "tt:RateControl/tt:FrameRateLimit"),
        "kbps": int(_text(cfg, "tt:RateControl/tt:BitrateLimit") or 0),
        "cbr": rc.get("ConstantBitRate") if rc is not None else None,
    }


class Media2Client:
    def __init__(self, host: str, port: int, user: str, password: str, timeout: int = 10):
        self._device_url = f"http://{host}:{int(port)}/onvif/device_service"
        self._user, self._password, self._timeout = user, password, timeout
        self._offset = None          # camera clock minus ours, for the digest's Created
        self._media2_url = None

    @classmethod
    def for_camera(cls, item: dict, timeout: int = 10) -> "Media2Client":
        """From a `cameras` registry row."""
        return cls(item["onvifHost"], int(item.get("onvifPort", 80)),
                   item["onvifUser"], item["onvifPassword"], timeout)

    # -- transport ------------------------------------------------------------------------

    def _security_header(self) -> str:
        nonce = os.urandom(16)
        created = (dt.datetime.now(dt.timezone.utc) + (self._offset or dt.timedelta(0))
                   ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        digest = base64.b64encode(hashlib.sha1(
            nonce + created.encode() + self._password.encode()).digest()).decode()
        return (f'<Security s:mustUnderstand="1" xmlns="{WSSE}"><UsernameToken>'
                f"<Username>{escape(self._user)}</Username>"
                f'<Password Type="{DIGEST}">{digest}</Password>'
                f'<Nonce EncodingType="{B64}">{base64.b64encode(nonce).decode()}</Nonce>'
                f'<Created xmlns="{WSU}">{created}</Created>'
                "</UsernameToken></Security>")

    def _call(self, url: str, body: str, auth: bool = True):
        header = f"<s:Header>{self._security_header()}</s:Header>" if auth else ""
        env = (f'<s:Envelope xmlns:s="{NS["s"]}" xmlns:tds="{NS["tds"]}" '
               f'xmlns:tr2="{NS["tr2"]}" xmlns:tt="{NS["tt"]}">'
               f"{header}<s:Body>{body}</s:Body></s:Envelope>")
        try:
            r = requests.post(url, data=env.encode(), timeout=self._timeout,
                              headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        except requests.RequestException as e:
            raise Media2Error(f"camera unreachable: {type(e).__name__}") from None
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            raise Media2Error(f"non-SOAP reply (HTTP {r.status_code})") from None
        fault = root.find(".//s:Fault", NS)
        if fault is not None:
            reason = " | ".join(t.strip() for t in fault.itertext() if t.strip())
            raise Media2Error(f"SOAP fault (HTTP {r.status_code}): {reason[:200]}")
        body_el = root.find("s:Body", NS)
        if body_el is None or len(body_el) == 0:
            raise Media2Error(f"empty SOAP body (HTTP {r.status_code})")
        return body_el[0]

    def _sync_clock(self):
        """WS-Security digests are time-stamped and cameras reject a skewed Created, so
        measure the camera's clock once (GetSystemDateAndTime needs no auth)."""
        if self._offset is not None:
            return
        try:
            resp = self._call(self._device_url, "<tds:GetSystemDateAndTime/>", auth=False)
            u = resp.find(".//tt:UTCDateTime", NS)
            cam = dt.datetime(int(_text(u, "tt:Date/tt:Year")), int(_text(u, "tt:Date/tt:Month")),
                              int(_text(u, "tt:Date/tt:Day")), int(_text(u, "tt:Time/tt:Hour")),
                              int(_text(u, "tt:Time/tt:Minute")), int(_text(u, "tt:Time/tt:Second")),
                              tzinfo=dt.timezone.utc)
            self._offset = cam - dt.datetime.now(dt.timezone.utc)
        except (Media2Error, TypeError, ValueError, AttributeError):
            self._offset = dt.timedelta(0)   # no usable clock: send ours and let auth decide

    @property
    def media2_url(self) -> str:
        if self._media2_url is None:
            self._sync_clock()
            resp = self._call(self._device_url, "<tds:GetServices><tds:IncludeCapability>"
                                                "false</tds:IncludeCapability></tds:GetServices>")
            for svc in resp.findall("tds:Service", NS):
                if _text(svc, "tds:Namespace") == NS["tr2"]:
                    self._media2_url = _text(svc, "tds:XAddr")
                    break
            else:
                raise Media2Error("camera advertises no Media2 service (ver20/media)")
        return self._media2_url

    # -- reads ----------------------------------------------------------------------------

    def profiles(self) -> list:
        """[{token, name, encoder_token, codec, stream_uri (redacted), stream_uri_raw}].
        stream_uri_raw carries credentials: match with it, never return or log it."""
        url = self.media2_url
        resp = self._call(url, "<tr2:GetProfiles><tr2:Type>All</tr2:Type></tr2:GetProfiles>")
        out = []
        for p in resp.findall("tr2:Profiles", NS):
            ve = p.find("tr2:Configurations/tr2:VideoEncoder", NS)
            uri = self._call(url, "<tr2:GetStreamUri><tr2:Protocol>RtspUnicast</tr2:Protocol>"
                                  f"<tr2:ProfileToken>{escape(p.get('token') or '')}</tr2:ProfileToken>"
                                  "</tr2:GetStreamUri>")
            raw = _text(uri, "tr2:Uri") or ""
            enc = _text(ve, "tt:Encoding")
            out.append({"token": p.get("token"), "name": _text(p, "tr2:Name"),
                        "encoder_token": ve.get("token") if ve is not None else None,
                        "codec": FROM_ONVIF.get(enc, enc),
                        "stream_uri": redact_uri(raw), "stream_uri_raw": raw})
        return out

    def encoder_config(self, token: str):
        resp = self._call(self.media2_url,
                          "<tr2:GetVideoEncoderConfigurations><tr2:ConfigurationToken>"
                          f"{escape(token)}</tr2:ConfigurationToken></tr2:GetVideoEncoderConfigurations>")
        cfg = resp.find("tr2:Configurations", NS)
        if cfg is None:
            raise Media2Error(f"no encoder configuration '{token}'")
        return cfg

    def encoder_options(self, token: str) -> dict:
        """{codec: {"resolutions": ["640x360", ...], "kbps": (min, max)}} for the codecs this
        project handles; anything else the camera offers (JPEG, ...) is left out."""
        resp = self._call(self.media2_url,
                          "<tr2:GetVideoEncoderConfigurationOptions><tr2:ConfigurationToken>"
                          f"{escape(token)}</tr2:ConfigurationToken></tr2:GetVideoEncoderConfigurationOptions>")
        out = {}
        for o in resp.findall("tr2:Options", NS):
            codec = FROM_ONVIF.get(_text(o, "tt:Encoding"))
            if not codec:
                continue
            out[codec] = {
                "resolutions": [f'{_text(r, "tt:Width")}x{_text(r, "tt:Height")}'
                                for r in o.findall("tt:ResolutionsAvailable", NS)],
                "kbps": (int(_text(o, "tt:BitrateRange/tt:Min") or 0),
                         int(_text(o, "tt:BitrateRange/tt:Max") or 0)),
            }
        return out

    # -- write ----------------------------------------------------------------------------

    def set_codec(self, token: str, codec: str) -> dict:
        """Switch encoder configuration `token` to `codec`, keeping resolution, frame rate,
        bitrate and GOP. Returns the read-back summary; raises Media2Error if the camera
        does not offer the codec at the current settings, or accepts the write but does not
        apply it.

        Verified on cam-02: the change sticks, the RTSP URI is unchanged, and MediaMTX's
        source reconnects by itself within ~7-10 s with the new track (codec-phase0.md §2).
        """
        if codec not in TO_ONVIF:
            raise Media2Error(f"unsupported codec '{codec}'")
        cfg = self.encoder_config(token)
        before = encoder_summary(cfg)
        if before["codec"] == codec:
            return before
        opts = self.encoder_options(token).get(codec)
        if not opts:
            raise Media2Error(f"encoder '{token}' does not offer {TO_ONVIF[codec]}")
        res = f"{before['width']}x{before['height']}"
        if res not in opts["resolutions"]:
            raise Media2Error(f"{TO_ONVIF[codec]} is not available at {res} "
                              f"(offered: {', '.join(opts['resolutions'])})")
        lo, hi = opts["kbps"]
        if hi and not lo <= before["kbps"] <= hi:
            raise Media2Error(f"{TO_ONVIF[codec]} bitrate range {lo}-{hi} kbps excludes "
                              f"the current {before['kbps']} kbps")

        cfg.find("tt:Encoding", NS).text = TO_ONVIF[codec]
        if "Profile" in cfg.attrib:
            # H.265 -> Main. Back to H.264 -> keep the H.264 profile the camera still reports
            # (cam-02 keeps saying "High" even while encoding H.265), else High.
            cfg.set("Profile", "Main" if codec == "h265"
                    else before["profile"] if before["profile"] in H264_PROFILES else "High")
        body = ET.tostring(cfg, encoding="unicode")
        body = body.replace("tr2:Configurations", "tr2:Configuration")   # Get -> Set element name
        self._call(self.media2_url,
                   f"<tr2:SetVideoEncoderConfiguration>{body}</tr2:SetVideoEncoderConfiguration>")

        after = encoder_summary(self.encoder_config(token))
        if after["codec"] != codec:
            raise Media2Error(f"camera accepted the change but still reports "
                              f"{TO_ONVIF.get(after['codec'], after['codec'])} -- write ignored")
        return after
