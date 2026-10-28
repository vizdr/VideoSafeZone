"""The clip-list Lambdas: list-clips (paging), play-clip, delete-clip, set-clip-tier
(PYTEST.md, Phase 2). S3 is a Stubber-checked client; the clips table is a FakeTable."""
from decimal import Decimal

import pytest

from fakes import invoke
from loaders import load_lambda

list_clips = load_lambda("list_clips")
play_clip = load_lambda("play_clip")
delete_clip = load_lambda("delete_clip")
set_clip_tier = load_lambda("set_clip_tier")

BUCKET = "test-bucket"
CAMERAS = [{"cameraId": "cam-02", "mode": "passthrough"}, {"cameraId": "cam-03", "mode": "passthrough"}]
KEY = "clips/cam-02/2026/10/25/120000.mp4"


def _clips(n, camera="cam-02"):
    """n clips, oldest first, one minute apart."""
    return [{"cameraId": camera, "startTs": f"2026-10-25T{10 + i // 60:02d}:{i % 60:02d}:00+00:00",
             "s3Key": f"clips/{camera}/2026/10/25/{i:04d}.mp4", "labels": ["motion"],
             "durationSec": Decimal("45"), "videoCodec": "h264"} for i in range(n)]


def _expect_heads(fake, keys, head=None):
    for key in keys:
        fake.stub("s3").add_response("head_object", head or {}, {"Bucket": BUCKET, "Key": key})


def _keys(rows):
    return [r["s3Key"] for r in rows]


# --- list-clips --------------------------------------------------------------------------------

def test_newest_clips_first_without_paging_fields(aws):
    rows = _clips(5)
    fake = aws(list_clips, CAMERAS, rows)
    newest = list(reversed(rows))[:2]
    _expect_heads(fake, _keys(newest))
    code, out = invoke(list_clips, query={"camera": "cam-02", "limit": "2"})
    assert code == 200 and set(out) == {"clips"}         # the old answer, for VMS (#48)
    assert [c["s3Key"] for c in out["clips"]] == _keys(newest)


def test_a_clip_row_as_the_page_shows_it(aws):
    rows = _clips(1)
    fake = aws(list_clips, CAMERAS, rows)
    _expect_heads(fake, _keys(rows), {"StorageClass": "DEEP_ARCHIVE",
                                      "Restore": 'ongoing-request="true"'})
    assert invoke(list_clips, query={"camera": "cam-02"})[1]["clips"] == [{
        "startTs": rows[0]["startTs"], "s3Key": rows[0]["s3Key"], "labels": ["motion"],
        "tier": "DEEP_ARCHIVE", "restoring": True, "durationSec": 45, "videoCodec": "h264"}]


def test_standard_tier_is_reported_when_s3_omits_it(aws):
    fake = aws(list_clips, CAMERAS, _clips(1))
    _expect_heads(fake, _keys(_clips(1)))
    assert invoke(list_clips, query={"camera": "cam-02"})[1]["clips"][0]["tier"] == "STANDARD"


def test_an_unreadable_head_is_unknown_not_a_failure(aws):
    fake = aws(list_clips, CAMERAS, _clips(1))
    fake.stub("s3").add_client_error("head_object", "404", http_status_code=404)
    clip = invoke(list_clips, query={"camera": "cam-02"})[1]["clips"][0]
    assert (clip["tier"], clip["restoring"]) == ("UNKNOWN", False)


def test_old_clips_have_no_duration_or_codec(aws):
    row = {k: v for k, v in _clips(1)[0].items() if k not in ("durationSec", "videoCodec")}
    fake = aws(list_clips, CAMERAS, [row])
    _expect_heads(fake, [row["s3Key"]])
    clip = invoke(list_clips, query={"camera": "cam-02"})[1]["clips"][0]
    assert (clip["durationSec"], clip["videoCodec"]) == (None, None)


def test_only_the_cameras_own_clips(aws):
    fake = aws(list_clips, CAMERAS, _clips(2, "cam-03") + _clips(1))
    _expect_heads(fake, _keys(_clips(1)))
    assert [c["s3Key"] for c in invoke(list_clips, query={"camera": "cam-02"})[1]["clips"]] == \
        _keys(_clips(1))


@pytest.mark.parametrize("page, expected_page, newest_first_slice", [
    ("1", 1, slice(0, 2)), ("2", 2, slice(2, 4)), ("3", 3, slice(4, 5)),
    ("99", 3, slice(4, 5)),        # past the end (deletes shrank the list): the last page
    ("last", 3, slice(4, 5)),
])
def test_ff51_paging(aws, page, expected_page, newest_first_slice):
    # Clips older than the newest 50 were unreachable from the browser (#51).
    rows = _clips(5)
    fake = aws(list_clips, CAMERAS, rows)
    want = list(reversed(rows))[newest_first_slice]
    _expect_heads(fake, _keys(want))
    code, out = invoke(list_clips, query={"camera": "cam-02", "limit": "2", "page": page})
    assert code == 200
    assert [c["s3Key"] for c in out["clips"]] == _keys(want)
    assert (out["page"], out["pages"], out["total"]) == (expected_page, 3, 5)


def test_ff51_paging_reads_every_page_of_keys(aws):
    # 120 clips in pages of 60 (DynamoDB's 1 MB pages): page 3 at 50 per page is the oldest 20.
    rows = _clips(120)
    fake = aws(list_clips, CAMERAS, rows, page_size=60)
    want = list(reversed(rows))[100:]
    _expect_heads(fake, _keys(want))
    out = invoke(list_clips, query={"camera": "cam-02", "page": "3"})[1]
    assert [c["s3Key"] for c in out["clips"]] == _keys(want)
    assert (out["page"], out["pages"], out["total"]) == (3, 3, 120)


def test_paging_with_no_clips(aws):
    aws(list_clips, CAMERAS, [])
    assert invoke(list_clips, query={"camera": "cam-02", "page": "1"})[1] == \
        {"clips": [], "page": 1, "pages": 1, "total": 0}


@pytest.mark.parametrize("query, phrase", [
    ({"camera": "cam-09"}, "unknown camera"),
    ({"camera": "cam-02", "limit": "x"}, "limit must be an integer"),
    ({"camera": "cam-02", "limit": "0"}, "between 1 and 50"),
    ({"camera": "cam-02", "limit": "51"}, "between 1 and 50"),
    ({"camera": "cam-02", "page": "0"}, "page must be"),
    ({"camera": "cam-02", "page": "first"}, "page must be"),
])
def test_list_clips_refuses(aws, query, phrase):
    aws(list_clips, CAMERAS, _clips(3))
    code, out = invoke(list_clips, query=query)
    assert code == 400 and phrase in out["error"]


# --- play-clip ---------------------------------------------------------------------------------

def _head(fake, **resp):
    fake.stub("s3").add_response("head_object", resp, {"Bucket": BUCKET, "Key": KEY})


@pytest.mark.parametrize("storage", [{}, {"StorageClass": "STANDARD_IA"},
                                     {"StorageClass": "DEEP_ARCHIVE",
                                      "Restore": 'ongoing-request="false", expiry-date="Mon, 27 Oct 2026 00:00:00 GMT"'}])
def test_a_readable_clip_gets_a_presigned_url(aws, storage):
    fake = aws(play_clip)
    _head(fake, ContentLength=5 * 1024 * 1024, **storage)
    code, out = invoke(play_clip, {"s3Key": KEY})
    assert code == 200 and out["status"] == "ready" and out["expires_in"] == 951
    assert out["url"].startswith(f"https://s3.eu-central-1.amazonaws.com/{BUCKET}/{KEY}?")
    assert "X-Amz-Expires=951" in out["url"]
    # The regional endpoint: a global hostname signed for eu-central-1 is a bare 400 from S3.
    assert fake.client_kwargs["s3"]["endpoint_url"] == "https://s3.eu-central-1.amazonaws.com"


def test_an_archived_clip_is_restored_first(aws):
    fake = aws(play_clip)
    _head(fake, StorageClass="DEEP_ARCHIVE", ContentLength=1000)
    fake.stub("s3").add_response("restore_object", {}, {
        "Bucket": BUCKET, "Key": KEY,
        "RestoreRequest": {"Days": 1, "GlacierJobParameters": {"Tier": "Standard"}}})
    assert invoke(play_clip, {"s3Key": KEY}) == (202, {"status": "restore_requested",
                                                       "eta_hours": 12})


def test_a_restore_in_progress_is_not_requested_again(aws):
    fake = aws(play_clip)
    _head(fake, StorageClass="DEEP_ARCHIVE", Restore='ongoing-request="true"')
    assert invoke(play_clip, {"s3Key": KEY}) == (202, {"status": "restoring", "eta_hours": 12})


@pytest.mark.parametrize("body", [{}, {"s3Key": ""}, {"s3Key": "secrets/x.mp4"}])
def test_play_only_clips(aws, body):
    aws(play_clip)
    assert invoke(play_clip, body)[0] == 400


def test_a_missing_clip_is_a_500_with_cors(aws):
    fake = aws(play_clip)
    fake.stub("s3").add_client_error("head_object", "404", http_status_code=404)
    assert invoke(play_clip, {"s3Key": KEY})[0] == 500


# --- delete-clip -------------------------------------------------------------------------------

def test_delete_removes_the_object_and_the_row(aws):
    rows = _clips(2)
    fake = aws(delete_clip, CAMERAS, rows)
    fake.stub("s3").add_response("delete_object", {}, {"Bucket": BUCKET, "Key": rows[0]["s3Key"]})
    assert invoke(delete_clip, {"s3Key": rows[0]["s3Key"], "startTs": rows[0]["startTs"]}) == \
        (200, {"status": "deleted"})
    assert fake.tables["clips"].items == [rows[1]]


@pytest.mark.parametrize("body", [{"s3Key": KEY}, {"startTs": "2026-10-25T12:00:00+00:00"},
                                  {"s3Key": "other/x.mp4", "startTs": "2026-10-25T12:00:00+00:00"}])
def test_delete_needs_a_clip_key_and_its_start(aws, body):
    fake = aws(delete_clip, CAMERAS, _clips(1))
    assert invoke(delete_clip, body)[0] == 400
    assert fake.tables["clips"].writes == []


# --- set-clip-tier -----------------------------------------------------------------------------

@pytest.mark.parametrize("current, target", [({}, "DEEP_ARCHIVE"),
                                             ({"StorageClass": "STANDARD_IA"}, "STANDARD")])
def test_tier_change_copies_in_place(aws, current, target):
    fake = aws(set_clip_tier)
    _head(fake, **current)
    fake.stub("s3").add_response("copy_object", {}, {
        "Bucket": BUCKET, "Key": KEY, "CopySource": {"Bucket": BUCKET, "Key": KEY},
        "StorageClass": target, "MetadataDirective": "COPY"})
    assert invoke(set_clip_tier, {"s3Key": KEY, "tier": target}) == \
        (200, {"status": "changed", "tier": target})


def test_same_tier_is_unchanged(aws):
    fake = aws(set_clip_tier)
    _head(fake, StorageClass="STANDARD_IA")
    assert invoke(set_clip_tier, {"s3Key": KEY, "tier": "STANDARD_IA"}) == (200, {"status": "unchanged"})


@pytest.mark.parametrize("restore", [None, 'ongoing-request="true"'])
def test_an_archived_clip_must_be_restored_before_moving_out(aws, restore):
    fake = aws(set_clip_tier)
    _head(fake, StorageClass="DEEP_ARCHIVE", **({"Restore": restore} if restore else {}))
    code, out = invoke(set_clip_tier, {"s3Key": KEY, "tier": "STANDARD"})
    assert code == 409 and "press Play first" in out["error"]


@pytest.mark.parametrize("tier", ["GLACIER", "standard", None])
def test_only_the_three_tiers(aws, tier):
    aws(set_clip_tier)
    assert invoke(set_clip_tier, {"s3Key": KEY, "tier": tier})[0] == 400
