"""vsz-registry (DEALER-PANEL-PLAN.md D13 as amended, Phase 10.2b): the adapters' writes to the camera
registry, through a route each Pi may call only for its own Thing.

What is pinned: only the adapters' role is answered; a Thing writes only its own rows -- another's row is
"no camera", a missing one is never made -- and never whose a row is or a claim token; conditions hold or
the write is refused; the adopting adapter (adapter-01) also writes, and stamps, rows without an owner,
never released ones or rows that aren't cameras (#93); a claimed ID is one past every adapter's and
taken only once; completing or abandoning a claim expects its token; a clip row only for one's own
camera, never over another; the scan's "another adapter's" mark from every row; nothing is printed.
IAM's half -- each Pi may sign only its own path -- is the device role's policy, tried on the Pi
(`registry_client.py check`).
"""
import json

import pytest

from loaders import load_lambda

reg = load_lambda("vsz_registry")

DEVICE = "arn:aws:sts::596633517506:assumed-role/KVSAdapterRole/0123456789abcdef"


def call(method, path, body=None, arn=DEVICE, raw=None):
    event = {"httpMethod": method, "pathParameters": {"proxy": path}}
    if arn is not None:
        event["requestContext"] = {"identity": {"userArn": arn}}
    if body is not None or raw is not None:
        event["body"] = raw if raw is not None else json.dumps(body)
    resp = reg.lambda_handler(event, None)
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*", resp
    return resp["statusCode"], json.loads(resp["body"])


@pytest.fixture
def adopting(monkeypatch):
    monkeypatch.setattr(reg, "ADOPTING_THING", "adapter-01")


def rows(aws, *cameras, clips=()):
    fake = aws(reg, cameras=list(cameras), clips=list(clips))
    return fake.tables["cameras"], fake.tables["clips"]


def cam(cid, owner=None, **extra):
    return {"cameraId": cid, "mode": "passthrough", **({"adapterId": owner} if owner else {}), **extra}


# --- who may call --------------------------------------------------------------------------------------

@pytest.mark.parametrize("arn", [None, "", "arn:aws:sts::596633517506:assumed-role/vsz-directory-api-role/x",
                                 "arn:aws:iam::596633517506:user/VladiZ"])
def test_only_the_adapters_role_is_answered(aws, arn):
    rows(aws)
    assert call("GET", "adapter-02/ping", arn=arn) == (403, {"error": "the registry route answers adapters only"})


def test_a_ping_says_who_is_asking(aws, adopting):
    rows(aws)
    assert call("GET", "adapter-02/ping") == (200, {"thing": "adapter-02", "adopting": False})
    assert call("GET", "adapter-01/ping") == (200, {"thing": "adapter-01", "adopting": True})
    assert call("GET", "admin/ping")[0] == 404                                # not an adapter's name
    assert call("GET", "adapter-02/cameras/cam-02")[0] == 405
    assert call("POST", "adapter-02/nothing", {})[0] == 404
    assert call("POST", "adapter-02/cameras/cam-02", raw="{not json")[0] == 400


# --- fields of one's own rows --------------------------------------------------------------------------

def test_a_thing_sets_and_removes_fields_of_its_own_row(aws):
    cams, _ = rows(aws, cam("cam-02", "adapter-02", videoCodec="h264", liveRtspUrl="rtsp://u:p@x/2"))
    status, out = call("POST", "adapter-02/cameras/cam-02",
                       {"set": {"videoCodec": "h265", "recordFps": 10.0, "updatedAt": "2026-11-15T10:00:00Z"},
                        "remove": ["liveRtspUrl"]})
    assert (status, out) == (200, {"updated": "cam-02"})
    row = cams.row("cam-02")
    assert (row["videoCodec"], row["recordFps"], "liveRtspUrl" in row) == ("h265", 10, False)
    [(_, w)] = cams.writes
    assert "= :v" in w["ConditionExpression"] and "adapterId" in w["ExpressionAttributeNames"].values()


def test_another_adapters_row_is_no_camera_and_a_missing_one_is_never_made(aws, adopting):
    cams, _ = rows(aws, cam("cam-03", "adapter-07", displayName="Theirs"))
    assert call("POST", "adapter-02/cameras/cam-03", {"set": {"displayName": "Mine"}}) == \
        (404, {"error": "no camera cam-03 on adapter-02"})
    assert call("POST", "adapter-01/cameras/cam-03", {"set": {"displayName": "Mine"}})[0] == 404
    assert cams.row("cam-03")["displayName"] == "Theirs"
    assert call("POST", "adapter-01/cameras/cam-99", {"set": {"displayName": "New"}})[0] == 404
    assert call("POST", "adapter-02/cameras/cam-99", {"set": {"displayName": "New"}})[0] == 404
    assert [r["cameraId"] for r in cams.items] == ["cam-03"]                  # nothing was made


def test_a_condition_that_doesnt_hold_refuses_the_write(aws):
    cams, _ = rows(aws, cam("cam-02", "adapter-02", onvifHost="192.0.2.12", onvifEndpointRef="urn:uuid:a"))
    move = {"set": {"onvifHost": "192.0.2.77"}}
    assert call("POST", "adapter-02/cameras/cam-02", {**move, "expect": {"onvifHost": "192.0.2.99"}}) == \
        (409, {"error": "the row doesn't meet the condition: it changed meanwhile"})
    assert call("POST", "adapter-02/cameras/cam-02", {**move, "missing": ["onvifEndpointRef"]})[0] == 409
    assert call("POST", "adapter-02/cameras/cam-02", {**move, "exists": ["unregisteredAt"]})[0] == 409
    assert cams.row("cam-02")["onvifHost"] == "192.0.2.12"
    assert call("POST", "adapter-02/cameras/cam-02",
                {**move, "expect": {"onvifHost": "192.0.2.12", "onvifEndpointRef": "urn:uuid:a"}})[0] == 200
    assert cams.row("cam-02")["onvifHost"] == "192.0.2.77"


@pytest.mark.parametrize("body", [
    {"set": {"adapterId": "adapter-02"}}, {"set": {"releasedFrom": "x"}}, {"set": {"cameraId": "cam-09"}},
    {"set": {"registering": "abc"}}, {"remove": ["adapterId"]}, {"remove": ["releasedFrom"]},
])
def test_whose_a_row_is_and_a_claim_token_are_never_written_by_an_adapter(aws, body):
    cams, _ = rows(aws, cam("cam-02", "adapter-02"))
    assert call("POST", "adapter-02/cameras/cam-02", body)[0] == 403
    assert cams.writes == []


@pytest.mark.parametrize("body", [
    {}, {"set": []}, {"set": {"bad name": 1}}, {"set": {"1x": 1}}, {"remove": "displayName"},
    {"set": {"a": 1}, "remove": ["a"]}, {"set": {"a": 1}, "expect": {"b": None}},
    {"set": {f"f{n}": n for n in range(101)}}, {"set": {"a": 1}, "exists": "b"},
])
def test_what_a_field_update_needs(aws, body):
    cams, _ = rows(aws, cam("cam-02", "adapter-02"))
    assert call("POST", "adapter-02/cameras/cam-02", body)[0] == 400
    assert cams.writes == []


def test_the_adopting_adapter_writes_rows_without_an_owner_and_no_one_else_does(aws, adopting):
    cams, _ = rows(aws, cam("cam-05"), cam("cam-06", releasedFrom="adapter-01"))
    assert call("POST", "adapter-01/cameras/cam-05", {"set": {"displayName": "Gate"}})[0] == 200
    assert cams.row("cam-05")["displayName"] == "Gate"
    assert call("POST", "adapter-02/cameras/cam-05", {"set": {"displayName": "Mine"}})[0] == 404
    assert call("POST", "adapter-01/cameras/cam-06", {"set": {"displayName": "Back"}})[0] == 404   # released
    assert "displayName" not in cams.row("cam-06")


def test_a_password_written_is_never_printed(aws, capsys):
    rows(aws, cam("cam-02", "adapter-02"))
    call("POST", "adapter-02/cameras/cam-02", {"set": {"onvifPassword": "s3cret-pw"}})
    call("POST", "adapter-02/cameras/cam-09", {"set": {"onvifPassword": "s3cret-pw"}})
    assert "s3cret-pw" not in capsys.readouterr().out


# --- claims: the next free camera ID (D11) -------------------------------------------------------------

def test_a_claim_takes_the_next_free_id_past_every_adapters(aws):
    cams, _ = rows(aws, cam("cam-01", "adapter-01"), cam("cam-07", "adapter-09"), {"cameraId": "cam01"})
    status, out = call("POST", "adapter-02/cameras/claim", {})
    assert status == 201 and out["cameraId"] == "cam-08" and len(out["token"]) == 16
    row = cams.row("cam-08")
    assert (row["adapterId"], row["registering"], bool(row["unregisteredAt"])) == ("adapter-02", out["token"], True)


def test_a_given_id_is_claimed_once(aws):
    rows(aws, cam("cam-04", "adapter-01"))
    assert call("POST", "adapter-02/cameras/claim", {"cameraId": "cam-12"})[1]["cameraId"] == "cam-12"
    assert call("POST", "adapter-02/cameras/claim", {"cameraId": "cam-04"}) == \
        (409, {"error": "cam-04 is taken -- leave the camera ID out and the next free one is used"})
    assert call("POST", "adapter-02/cameras/claim", {"cameraId": "camera-4"})[0] == 400


def test_an_id_taken_meanwhile_is_followed_by_the_next(aws, monkeypatch):
    cams, _ = rows(aws, cam("cam-03", "adapter-01"))
    scan = cams.scan
    seen = iter([{"Items": [{"cameraId": "cam-02"}]}])          # a stale first look: cam-03 seems free

    def scan_once_stale(**kw):
        try:
            return next(seen)
        except StopIteration:
            return scan(**kw)
    monkeypatch.setattr(cams, "scan", scan_once_stale)
    assert call("POST", "adapter-02/cameras/claim", {})[1]["cameraId"] == "cam-04"


def test_the_next_free_id_counts_every_page_and_only_camera_ids(aws):
    # Moved here from the Pi's next_camera_id (DEALER-PANEL-PLAN.md Phase 10.2c): the route alone sees
    # every adapter's rows. Not camera IDs (a live stream's name, a stray row) don't count.
    fake = aws(reg, cameras=[cam("cam-01", "adapter-01"), cam("cam-02", "adapter-02"), cam("cam-99", "adapter-07"),
                             {"cameraId": "cam-03-live"}, {"cameraId": "other"}], page_size=2)
    assert call("POST", "adapter-02/cameras/claim", {})[1]["cameraId"] == "cam-100"
    assert fake.tables["cameras"].row("cam-100")["adapterId"] == "adapter-02"
    rows(aws)
    assert call("POST", "adapter-02/cameras/claim", {})[1]["cameraId"] == "cam-01"


def test_no_id_past_cam_9999(aws):
    rows(aws, cam("cam-9999", "adapter-01"))
    assert call("POST", "adapter-02/cameras/claim", {}) == (409, {"error": "no camera ID left (cam-9999 is the last)"})


def test_completing_or_abandoning_a_claim_expects_its_token(aws):
    cams, _ = rows(aws)
    token = call("POST", "adapter-02/cameras/claim", {"cameraId": "cam-20"})[1]["token"]
    complete = {"set": {"mode": "passthrough", "displayName": "Yard"}, "remove": ["registering", "unregisteredAt"]}
    assert call("POST", "adapter-02/cameras/cam-20", {**complete, "expect": {"registering": "wrong"}})[0] == 409
    assert call("POST", "adapter-02/cameras/cam-20", {**complete, "expect": {"registering": token}})[0] == 200
    row = cams.row("cam-20")
    assert (row["displayName"], "registering" in row, "unregisteredAt" in row) == ("Yard", False, False)


# --- adopting, clips, the scan's mark --------------------------------------------------------------------

def test_the_adopting_adapter_stamps_a_camera_without_an_owner(aws, adopting):
    cams, _ = rows(aws, cam("cam-05"), cam("cam-06", "adapter-09"), cam("cam-07", releasedFrom="adapter-01"),
                   {"cameraId": "cam01", "videoCodecActive": "h264"})
    assert call("POST", "adapter-01/cameras/cam-05/adopt") == (200, {"adopted": "cam-05"})
    assert cams.row("cam-05")["adapterId"] == "adapter-01"
    for taken in ("cam-06", "cam-07"):
        assert call("POST", f"adapter-01/cameras/{taken}/adopt")[0] == 409
    assert cams.row("cam-06")["adapterId"] == "adapter-09" and "adapterId" not in cams.row("cam-07")
    assert call("POST", "adapter-02/cameras/cam-05/adopt")[0] == 403


def test_ff93_a_row_that_isnt_a_camera_is_never_adopted_through_the_route(aws, adopting):
    """FoundAndFixed.md #93, kept by the route: a row with no mode is not a camera."""
    cams, _ = rows(aws, {"cameraId": "cam-08", "videoCodecActive": "h264"})
    assert call("POST", "adapter-01/cameras/cam-08/adopt")[0] == 409
    assert "adapterId" not in cams.row("cam-08")


CLIP = {"cameraId": "cam-02", "startTs": "2026-11-15T09:00:00+00:00", "s3Key": "clips/cam-02/adapter-02/2026/11/15/090000-outage.mp4",
        "labels": ["outage"], "durationSec": 120, "videoCodec": "h264"}


def test_a_clip_row_only_for_ones_own_camera_and_never_over_another(aws):
    _, clips = rows(aws, cam("cam-02", "adapter-02"), cam("cam-03", "adapter-07"))
    assert call("POST", "adapter-02/clips", {"item": CLIP}) == \
        (201, {"clip": {"cameraId": "cam-02", "startTs": CLIP["startTs"]}})
    assert clips.row("cam-02", CLIP["startTs"])["durationSec"] == 120
    assert call("POST", "adapter-02/clips", {"item": CLIP})[0] == 409
    assert call("POST", "adapter-02/clips", {"item": {**CLIP, "cameraId": "cam-03",
                                                      "s3Key": CLIP["s3Key"].replace("cam-02", "cam-03")}}) == \
        (404, {"error": "no camera cam-03 on adapter-02"})


@pytest.mark.parametrize("key, ok", [
    ("clips/cam-02/adapter-02/2026/11/15/090000-outage.mp4", True),     # its camera, under its Thing (10.2e)
    ("clips/cam-02/2026/11/15/090000-outage.mp4", True),                # an uploader not yet restarted (until 10.2f)
    ("clips/cam-02/adapter-07/2026/11/15/090000-outage.mp4", False),    # under another adapter's Thing
    ("clips/cam-03/adapter-02/2026/11/15/090000-outage.mp4", False),    # another camera's key
    ("clips/cam-02/x.mp4", False), ("thumbs/adapter-02/cam-02.jpg", False), ("clips/cam-02", False),
])
def test_a_clip_rows_key_is_its_cameras_under_its_own_thing(aws, key, ok):
    # DEALER-PANEL-PLAN.md Phase 10.2e: the camera stays where every clip reader takes it from.
    _, clips = rows(aws, cam("cam-02", "adapter-02"), cam("cam-03", "adapter-02"))
    status, out = call("POST", "adapter-02/clips", {"item": {**CLIP, "s3Key": key}})
    assert (status == 201) is ok, (status, out)
    if not ok:
        assert status == 400 and "clips/cam-02/adapter-02/" in out["error"] and clips.items == []


@pytest.mark.parametrize("item", [
    {k: v for k, v in CLIP.items() if k != "labels"}, {**CLIP, "extra": 1}, {**CLIP, "s3Key": "thumbs/x.jpg"},
    {**CLIP, "cameraId": "cam02"}, None,
])
def test_what_a_clip_row_needs(aws, item):
    _, clips = rows(aws, cam("cam-02", "adapter-02"))
    assert call("POST", "adapter-02/clips", {"item": item})[0] == 400
    assert clips.items == []


def test_the_scan_learns_which_devices_are_another_adapters(aws, adopting):
    rows(aws, cam("cam-02", "adapter-02", onvifEndpointRef="urn:uuid:mine"),
         cam("cam-03", "adapter-07", onvifEndpointRef="urn:uuid:theirs"),
         cam("cam-04", onvifEndpointRef="urn:uuid:unstamped"),
         cam("cam-05", releasedFrom="adapter-02", onvifEndpointRef="urn:uuid:released"))
    refs = ["urn:uuid:mine", "urn:uuid:theirs", "urn:uuid:unstamped", "urn:uuid:released", "urn:uuid:new"]
    assert call("POST", "adapter-02/devices", {"endpointRefs": refs}) == \
        (200, {"elsewhere": ["urn:uuid:released", "urn:uuid:theirs", "urn:uuid:unstamped"]})
    assert call("POST", "adapter-01/devices", {"endpointRefs": refs})[1]["elsewhere"] == \
        ["urn:uuid:mine", "urn:uuid:released", "urn:uuid:theirs"]          # unstamped rows are adapter-01's
    assert call("POST", "adapter-02/devices", {"endpointRefs": "urn:uuid:mine"})[0] == 400
