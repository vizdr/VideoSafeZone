"""Tiny MP4 files built box by box, a few bytes each (PYTEST.md, step 5).

Only the structure the clip Lambdas walk is real: ftyp, moov/mvhd for the duration, and a sample
entry deep in moov for the codec. Nothing here plays.
"""


def box(name: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + name + payload


def large_box(name: bytes, payload: bytes) -> bytes:
    """size 1: the real size follows as 64 bits."""
    return (1).to_bytes(4, "big") + name + (16 + len(payload)).to_bytes(8, "big") + payload


def mvhd_v0(timescale: int, duration: int) -> bytes:
    return box(b"mvhd", bytes(4) + bytes(4) + bytes(4) + timescale.to_bytes(4, "big")
               + duration.to_bytes(4, "big") + bytes(80))


def mvhd_v1(timescale: int, duration: int) -> bytes:
    return box(b"mvhd", b"\x01\x00\x00\x00" + bytes(8) + bytes(8) + timescale.to_bytes(4, "big")
               + duration.to_bytes(8, "big") + bytes(80))


def sample_entry(fourcc: bytes) -> bytes:
    return box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", box(b"stsd", bytes(8) + box(fourcc))))))


FTYP_SAYS_AVC1 = box(b"ftyp", b"isom" + bytes(4) + b"isomavc1")


def clip(seconds: int, fourcc: bytes = b"avc1") -> bytes:
    """What GetClip returns: an MP4 holding `seconds` of video in the codec of `fourcc`."""
    return FTYP_SAYS_AVC1 + box(b"moov", mvhd_v0(1000, seconds * 1000) + sample_entry(fourcc)) \
        + box(b"mdat", bytes(16))
