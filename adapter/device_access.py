"""What this adapter's device role lets it do, tried on the Pi (DEALER-PANEL-PLAN.md D13, Phase 10.2f).

After `provision.sh narrow` the device role reaches only this adapter's own resources. This tries, with
the Pi's own credentials, that what it needs is allowed and that the wide access it had is gone -- only
by probes that change nothing whichever way they come out: reads, a write whose condition can never
hold, a refused call. What would write if it were allowed (another adapter's S3 key, an untagged
stream, an MQTT event) isn't tried; the thumbnails timer and the producers show the allowed side every
few minutes. Before the narrowing the "refused" lines fail -- that is the wide role, as expected.

  venv-adapter/bin/python3 adapter/device_access.py check

It also runs registry_reads' and camera_secrets' checks (the index, and the secrets in SSM).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402

ALLOWED, REFUSED = "allowed", "refused"


def _code(e):
    return getattr(e, "response", {}).get("Error", {}).get("Code")


def probe(call):
    """'allowed' or 'refused', or the error code of anything else (a call that failed for another
    reason proves nothing either way). A ConditionalCheckFailed is 'allowed': IAM let the write through
    to its condition."""
    from botocore.exceptions import ClientError
    try:
        call()
        return ALLOWED
    except ClientError as e:
        code = _code(e)
        if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation"):
            return REFUSED
        if code in ("ConditionalCheckFailedException", "ResourceNotFoundException"):
            return ALLOWED
        return code or type(e).__name__


def check(session=None):
    """Returns how many expectations failed."""
    import registry_reads
    import camera_secrets
    from boto3.dynamodb.conditions import Key
    if session is None:
        from aws_device_creds import get_session
        session = get_session()
    ddb = session.resource("dynamodb")
    cameras, clips, requests = ddb.Table("cameras"), ddb.Table("clips"), ddb.Table("camera-requests")
    kv = session.client("kinesisvideo")
    failed = 0

    def expect(want, what, call):
        nonlocal failed
        got = probe(call)
        failed += got != want
        print(f"{'PASS' if got == want else 'FAIL'}  {what}: {got}", flush=True)

    thing = config.THING_NAME
    expect(ALLOWED, "its rows, by the adapterId-index",
           lambda: cameras.query(IndexName=registry_reads.INDEX, KeyConditionExpression=Key("adapterId").eq(thing), Limit=1))
    expect(REFUSED, "the whole camera table (Scan)", lambda: cameras.scan(Limit=1))
    expect(REFUSED, "a camera row by its key (GetItem)", lambda: cameras.get_item(Key={"cameraId": "cam-01"}))
    expect(REFUSED, "a clip row written directly (PutItem, a condition that never holds)",
           lambda: clips.put_item(Item={"cameraId": "cam-00", "startTs": "0000"},
                                  ConditionExpression="attribute_exists(cameraId) AND attribute_not_exists(cameraId)"))
    expect(REFUSED, "a camera request read directly (GetItem)",
           lambda: requests.get_item(Key={"requestId": "00000000-device-access-check"}))
    for row in registry_reads.own_rows(["mode"], session=session, secrets=False):
        if row.get("mode") not in ("transcode", "passthrough"):
            continue
        # The recorded stream, which every registered camera has; a live view's is made on demand, and a
        # stream that doesn't exist can't show its tag (refused, whoever asks).
        expect(ALLOWED, f"its stream {row['cameraId']} (DescribeStream, by its tag)",
               lambda cid=row["cameraId"]: kv.describe_stream(StreamName=cid))
    expect(ALLOWED, f"a thumbnail under thumbs/{thing}/ (PutObject)",
           lambda: session.client("s3").put_object(Bucket=config.EVIDENCE_BUCKET,
                                                   Key=f"thumbs/{thing}/.device-access-check", Body=b"",
                                                   CacheControl="no-cache"))
    print("-- the index", flush=True)
    failed += registry_reads.check(session)
    print("-- camera secrets", flush=True)
    failed += camera_secrets.check(session)
    return failed


if __name__ == "__main__":
    if sys.argv[1:] != ["check"]:
        sys.exit("usage: device_access.py check")
    sys.exit(1 if check() else 0)
