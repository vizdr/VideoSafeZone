"""Each camera's secrets in SSM, per adapter (DEALER-PANEL-PLAN.md D13, Phase 10.2d; adapter/camera_secrets.py).

The ONVIF password and the RTSP URIs (which carry the camera's login) are kept under
/vsz/adapters/<this Thing>/cameras/<cameraId>/<field>. Until the role is narrowed (10.2f) the row keeps
them too and stays what readers use, so SSM is written beside every row write (registry_client.update),
the rows are mirrored into it by rematch, and a row is filled from it only when it holds no secrets.
The fake SSM (tests/fakes.py) refuses another Thing's path, as IAM does; the fixture's Thing is test-thing.
"""
import json
import os
import stat
import sys

import pytest

import camera_secrets
import config
import mediamtx_api
import onvif_discovery
import registry_client
import registry_reads
import sync_mediamtx_paths
from fakes import FakeMediaMTX

PW, MAIN, SUB = "s3cret-pw", "rtsp://admin:s3cret-pw@192.0.2.13:554/main", "rtsp://admin:s3cret-pw@192.0.2.13:554/sub"
CAM03 = {"cameraId": "cam-03", "adapterId": "test-thing", "mode": "passthrough", "onvifHost": "192.0.2.13",
         "onvifUser": "admin", "onvifPassword": PW, "rtspUrl": MAIN, "liveRtspUrl": SUB}
CAM04 = {"cameraId": "cam-04", "adapterId": "test-thing", "mode": "passthrough", "onvifHost": "192.0.2.14",
         "onvifUser": "admin", "onvifPassword": "", "rtspUrl": "rtsp://192.0.2.14:554/0"}    # no password
USB = {"cameraId": "cam-01", "adapterId": "test-thing", "mode": "transcode"}


def name(cid, field):
    return f"/vsz/adapters/test-thing/cameras/{cid}/{field}"


def stored(ssm, cid):
    return {n.rsplit("/", 1)[1]: json.loads(v) for n, v in ssm.params.items() if n.startswith(name(cid, ""))}


# --- read, write --------------------------------------------------------------------------------------

def test_each_field_is_its_own_securestring_under_this_things_path(registry):
    table = registry([])
    camera_secrets.write("cam-03", {"onvifPassword": PW, "rtspUrl": MAIN, "onvifUser": "admin", "mode": "x"})
    assert set(table.session.ssm.params) == {name("cam-03", "onvifPassword"), name("cam-03", "rtspUrl")}  # secrets only
    assert camera_secrets.read("cam-03") == {"onvifPassword": PW, "rtspUrl": MAIN}
    assert camera_secrets.read("cam-42") == {}


def test_an_empty_password_is_kept(registry):
    # SSM refuses an empty value; cam-04 has no password at all, and that must survive the round trip.
    registry([])
    camera_secrets.write("cam-04", {"onvifPassword": ""})
    assert camera_secrets.read("cam-04") == {"onvifPassword": ""}


def test_a_removed_field_is_deleted_and_one_not_there_is_no_error(registry):
    table = registry([])
    camera_secrets.write("cam-03", {"rtspUrl": MAIN, "liveRtspUrl": SUB})
    camera_secrets.write("cam-03", remove=["liveRtspUrl", "liveRtspUrl", "displayName"])
    camera_secrets.write("cam-03", remove=["liveRtspUrl"])
    assert stored(table.session.ssm, "cam-03") == {"rtspUrl": MAIN}


def test_another_things_path_is_never_used(registry, monkeypatch):
    table = registry([])
    camera_secrets.write("cam-03", {"rtspUrl": MAIN})
    monkeypatch.setattr(config, "THING_NAME", "adapter-02")    # the same camera ID on another adapter
    assert camera_secrets.read("cam-03") == {}
    assert camera_secrets.path("cam-03") == "/vsz/adapters/adapter-02/cameras/cam-03"
    assert list(table.session.ssm.params) == [name("cam-03", "rtspUrl")]


# --- beside every registry write ------------------------------------------------------------------------

def test_a_secret_written_to_the_row_is_written_to_ssm_too(registry):
    table = registry([CAM03])
    registry_client.update("cam-03", set={"rtspUrl": MAIN + "2", "updatedAt": "t"}, remove=["liveRtspUrl"])
    assert table.row("cam-03")["rtspUrl"] == MAIN + "2" and "liveRtspUrl" not in table.row("cam-03")
    assert stored(table.session.ssm, "cam-03") == {"rtspUrl": MAIN + "2"}


def test_a_write_without_secrets_asks_ssm_nothing(registry):
    table = registry([CAM03])
    registry_client.update("cam-03", set={"recordingMode": "manual"})
    assert table.session.ssm.calls == []


def test_a_refused_ssm_write_never_fails_the_registry_write_and_prints_no_secret(registry, monkeypatch, capsys):
    table = registry([CAM03])

    def refused(**kw):
        raise camera_secrets_error("AccessDeniedException")
    monkeypatch.setattr(table.session.ssm, "put_parameter", refused)
    registry_client.update("cam-03", set={"onvifPassword": "new-" + PW})
    assert table.row("cam-03")["onvifPassword"] == "new-" + PW           # the row has it; the mirror copies it later
    out = capsys.readouterr().out
    assert "onvifPassword not written to SSM (AccessDeniedException)" in out and PW not in out


def camera_secrets_error(code):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "PutParameter")


# --- read: the row while it holds them, SSM once it doesn't ---------------------------------------------

def test_a_row_that_holds_its_secrets_is_read_as_it_is_and_ssm_is_never_asked(registry):
    table = registry([CAM03, USB])
    table.session.ssm.params[name("cam-03", "onvifPassword")] = json.dumps("stale")   # VMS changed the row since
    rows = {r["cameraId"]: r for r in registry_reads.own_rows()}
    assert rows["cam-03"]["onvifPassword"] == PW and rows["cam-01"] == USB
    assert registry_reads.own_row("cam-03")["onvifPassword"] == PW
    assert table.session.ssm.calls == []


def test_a_row_without_its_secrets_gets_them_from_ssm(registry):
    # After the narrowing (10.2f) the rows hold none: every reader still gets the whole camera.
    bare = {k: v for k, v in CAM03.items() if k not in camera_secrets.FIELDS}
    table = registry([bare, USB])
    camera_secrets.write("cam-03", {"onvifPassword": PW, "rtspUrl": MAIN})
    assert registry_reads.own_row("cam-03") == {**bare, "onvifPassword": PW, "rtspUrl": MAIN}
    assert [r.get("rtspUrl") for r in registry_reads.own_rows()] == [None, MAIN]          # cam-01: none to fill
    assert registry_reads.own_rows(["rtspUrl"])[1]["rtspUrl"] == MAIN                    # asked for by field too
    table.session.ssm.calls.clear()
    assert "rtspUrl" not in registry_reads.own_rows(["schedule"])[1]                     # not asked: no SSM call
    assert table.session.ssm.calls == []


def test_ssm_unreachable_fails_a_read_that_needs_it(registry, monkeypatch):
    bare = {k: v for k, v in CAM03.items() if k not in camera_secrets.FIELDS}
    table = registry([bare])

    def down(**kw):
        raise OSError("offline")
    monkeypatch.setattr(table.session.ssm, "get_parameters", down)
    with pytest.raises(OSError):
        registry_reads.own_row("cam-03")


# --- the mirror -------------------------------------------------------------------------------------------

def test_the_mirror_copies_every_row_that_holds_secrets(registry, capsys):
    table = registry([CAM03, CAM04, USB])
    assert camera_secrets.mirror(now=1000) == ["cam-03", "cam-04"]
    assert stored(table.session.ssm, "cam-03") == {"onvifPassword": PW, "rtspUrl": MAIN, "liveRtspUrl": SUB}
    assert stored(table.session.ssm, "cam-04") == {"onvifPassword": "", "rtspUrl": CAM04["rtspUrl"]}
    out = capsys.readouterr().out
    assert "cam-03: secrets mirrored to SSM (liveRtspUrl, onvifPassword, rtspUrl)" in out and PW not in out


def test_a_camera_is_asked_about_again_only_when_its_row_changes_or_after_a_day(registry):
    table = registry([CAM03])
    camera_secrets.mirror(now=1000)
    table.session.ssm.calls.clear()
    assert camera_secrets.mirror(now=1000 + camera_secrets.MIRROR_AGAIN_AFTER - 1) == []
    assert table.session.ssm.calls == []                                     # not even a read
    assert camera_secrets.mirror(now=1000 + camera_secrets.MIRROR_AGAIN_AFTER) == []
    assert [c[0] for c in table.session.ssm.calls] == ["get"] * 3            # asked, nothing to write
    table.row("cam-03")["rtspUrl"] = MAIN + "?moved"                          # VMS's card changed the row
    table.session.ssm.calls.clear()
    assert camera_secrets.mirror(now=1001 + camera_secrets.MIRROR_AGAIN_AFTER) == ["cam-03"]
    assert ("put", name("cam-03", "rtspUrl")) in table.session.ssm.calls
    assert stored(table.session.ssm, "cam-03")["rtspUrl"] == MAIN + "?moved"


def test_what_the_row_no_longer_has_is_deleted_from_ssm(registry):
    table = registry([CAM03])
    camera_secrets.mirror(now=1000)
    del table.row("cam-03")["liveRtspUrl"]
    camera_secrets.mirror(now=1001)
    assert "liveRtspUrl" not in stored(table.session.ssm, "cam-03")


def test_a_dry_run_writes_and_remembers_nothing(registry, capsys):
    table = registry([CAM03])
    assert camera_secrets.mirror(dry_run=True, now=1000) == ["cam-03"]
    assert table.session.ssm.params == {}
    assert not (sync_mediamtx_paths.STATE_DIR / "secrets-mirrored.json").exists()
    assert "would mirror" in capsys.readouterr().out


def test_the_record_holds_no_secret_and_only_its_owner_reads_it(registry):
    registry([CAM03])
    camera_secrets.mirror(now=1000)
    record = sync_mediamtx_paths.STATE_DIR / "secrets-mirrored.json"
    assert PW not in record.read_text() and MAIN not in record.read_text()
    assert stat.S_IMODE(os.stat(record).st_mode) == 0o600


def test_a_row_without_secrets_is_left_to_ssm(registry):
    bare = {k: v for k, v in CAM03.items() if k not in camera_secrets.FIELDS}
    table = registry([bare])
    camera_secrets.write("cam-03", {"onvifPassword": PW, "rtspUrl": MAIN})
    table.session.ssm.calls.clear()
    assert camera_secrets.mirror(now=1000) == []
    assert stored(table.session.ssm, "cam-03") == {"onvifPassword": PW, "rtspUrl": MAIN}       # never deleted
    assert table.session.ssm.calls == []                     # nor even read: nothing in the row to compare


def test_rematch_runs_the_mirror_and_a_refusal_only_skips_it(registry, monkeypatch, capsys):
    import rematch_cameras as rc
    table = registry([CAM03])
    mtx = FakeMediaMTX(conf={"cam03": {"source": MAIN, "rtspTransport": "tcp"}})
    monkeypatch.setattr(mediamtx_api, "requests", mtx)
    monkeypatch.setattr(sync_mediamtx_paths, "requests", mtx)
    monkeypatch.setattr(onvif_discovery, "locate", lambda timeout: {})
    monkeypatch.setattr(sys, "argv", ["rematch_cameras.py"])
    monkeypatch.setattr(rc, "tag_own_streams", lambda dry_run=False: [])
    assert rc.main() == 0
    assert set(stored(table.session.ssm, "cam-03")) == {"onvifPassword", "rtspUrl", "liveRtspUrl"}

    def refused(**kw):
        raise camera_secrets_error("AccessDeniedException")
    monkeypatch.setattr(table.session.ssm, "get_parameters", refused)
    (sync_mediamtx_paths.STATE_DIR / "secrets-mirrored.json").unlink()
    assert rc.main() == 0
    out = capsys.readouterr().out
    assert "mirroring camera secrets skipped (AccessDeniedException)" in out and PW not in out


# --- check ------------------------------------------------------------------------------------------------

def test_check_passes_once_mirrored_and_fails_before(registry, capsys):
    registry([CAM03, CAM04, USB])
    assert camera_secrets.check() == 2                                      # neither mirrored yet
    camera_secrets.mirror(now=1000)
    assert camera_secrets.check() == 0
    out = capsys.readouterr().out
    assert "PASS  IAM refuses adapter-99's camera secrets to this Pi: AccessDeniedException" in out
    assert "PASS  cam-03: SSM holds the row's secrets: liveRtspUrl, onvifPassword, rtspUrl" in out
    assert PW not in out and MAIN not in out


def test_check_of_a_row_that_holds_no_secrets_asks_ssm_alone(registry, capsys):
    # After the narrowing (10.2f): the row holds none, and SSM must have them.
    bare = {k: v for k, v in CAM03.items() if k not in camera_secrets.FIELDS}
    registry([bare])
    assert camera_secrets.check() == 1
    assert "FAIL  cam-03: its secrets in SSM only: none" in capsys.readouterr().out
    camera_secrets.write("cam-03", {"onvifPassword": PW, "rtspUrl": MAIN})
    assert camera_secrets.check() == 0
    assert "PASS  cam-03: its secrets in SSM only: onvifPassword, rtspUrl" in capsys.readouterr().out


def test_check_fails_when_another_things_secrets_are_answered(registry, monkeypatch, capsys):
    table = registry([])
    monkeypatch.setattr(table.session.ssm, "get_parameters",
                        lambda Names, WithDecryption=False: {"Parameters": [], "InvalidParameters": Names})
    assert camera_secrets.check() == 1
    assert "FAIL  IAM refuses adapter-99's camera secrets" in capsys.readouterr().out


def test_the_role_may_reach_only_its_own_things_secrets():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    [st] = json.loads((root / "cloud/iam/kvs-adapter-camera-secrets-policy.json").read_text())["Statement"]
    assert sorted(st["Action"]) == ["ssm:DeleteParameter", "ssm:GetParameters", "ssm:PutParameter"]
    assert st["Resource"].endswith(":parameter/vsz/adapters/${credentials-iot:ThingName}/*")
    assert "Condition" not in st


# --- strip: the rows rid of their secrets, after the narrowing (Phase 10.2f) ----------------------------

def test_strip_takes_the_secrets_out_of_rows_whose_ssm_copy_is_whole(registry, monkeypatch, capsys):
    table = registry([CAM03, CAM04, USB])
    monkeypatch.setattr(table.route.lam, "ROW_SECRETS", "drop")         # provision.sh narrow sets it
    camera_secrets.mirror(now=1000)
    before = dict(table.session.ssm.params)
    assert camera_secrets.strip() == 0
    for cid in ("cam-03", "cam-04"):
        assert not set(camera_secrets.FIELDS) & set(table.row(cid))
    assert table.session.ssm.params == before                            # SSM untouched
    assert registry_reads.own_row("cam-03")["onvifPassword"] == PW       # readers now get them from SSM
    out = capsys.readouterr().out
    assert "cam-03: secrets in SSM only" in out and PW not in out and MAIN not in out


def test_strip_leaves_a_row_whose_ssm_copy_differs(registry, monkeypatch, capsys):
    table = registry([CAM03])
    monkeypatch.setattr(table.route.lam, "ROW_SECRETS", "drop")
    camera_secrets.write("cam-03", {"onvifPassword": PW, "rtspUrl": MAIN})        # no liveRtspUrl
    assert camera_secrets.strip() == 1
    assert table.row("cam-03")["liveRtspUrl"] == SUB and table.row("cam-03")["onvifPassword"] == PW
    assert "cam-03: SSM doesn't hold the row's secrets (liveRtspUrl differ)" in capsys.readouterr().out


def test_strip_before_the_route_drops_secrets_says_so(registry, capsys):
    table = registry([CAM03])                                             # ROW_SECRETS: keep
    camera_secrets.mirror(now=1000)
    assert camera_secrets.strip() == 1
    assert table.row("cam-03")["onvifPassword"] == PW
    assert "is vsz-registry's ROW_SECRETS=drop set" in capsys.readouterr().out
