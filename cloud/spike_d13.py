#!/usr/bin/env python3
"""The device's side of the D13 spike (DEALER-PANEL-PLAN.md Phase 10.1), run by `cloud/spike-d13.sh run`.

Everything here uses the spike Thing's certificate alone -- the credentials an adapter called
vsz-spike-a would get -- and asks AWS for its own resources and for another adapter's (vsz-spike-b).
The role's policy (cloud/iam/vsz-spike-d13-policy.json) has D13's shape: secrets, streams, rows through
an index, S3 keys and topics, each scoped by ${credentials-iot:ThingName}. Each check prints what was
expected, what happened and AWS's own error code, and the whole set goes to results.json in the spike
folder. Whether video reached a stream is KVS's to say: `spike-d13.sh verify` counts the fragments,
with the owner's login, after this has run.

Needs: the spike folder from `spike-d13.sh setup`, /etc/adapter/adapter.env (the credentials endpoint,
the region), boto3 (venv-adapter), and for PutMedia the KVS GStreamer plugin under vendor/.
"""
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VMS_HOME = Path(os.environ.get("VMS_HOME") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(VMS_HOME / "adapter"))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

import config  # noqa: E402  -- AWS_REGION, IOT_CRED_ENDPOINT

THING, OTHER = "vsz-spike-a", "vsz-spike-b"
ALIAS = "VszSpikeD13RoleAlias"
FOLDER = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "videosafezone" / "spike-d13"
BUCKET = "vsz-spike-596633517506"
TABLE, INDEX = "vsz-spike-cameras", "adapterId-index"
SDK = VMS_HOME / "vendor" / "amazon-kinesis-video-streams-producer-sdk-cpp"
DENIED = {"AccessDeniedException", "AccessDenied", "ForbiddenException", "UnauthorizedException",
          "UnrecognizedClientException", "NotAuthorized"}

results = []


def credentials(header, wait=0):
    """A session from the credentials endpoint with the spike certificate; `header` is the Thing name
    sent in x-amzn-iot-thingname, or None for no header. A role alias setup has just made is unknown to
    the endpoint for a few minutes (404 -- measured 2026-11-15: gone within 4 minutes, FoundAndFixed.md
    #95), so with `wait` a 404 or 403 is asked again until that many seconds have passed."""
    ctx = ssl.create_default_context(cafile=str(FOLDER / "cacert.pem"))
    ctx.load_cert_chain(certfile=str(FOLDER / "adapter.cert.pem"), keyfile=str(FOLDER / "adapter.private.key"))
    req = urllib.request.Request(f"https://{config.IOT_CRED_ENDPOINT}/role-aliases/{ALIAS}/credentials",
                                 headers={"x-amzn-iot-thingname": header} if header else {})
    deadline = time.monotonic() + wait
    while True:
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
                c = json.load(r)["credentials"]
            break
        except urllib.error.HTTPError as e:
            if e.code not in (403, 404) or time.monotonic() >= deadline:
                raise
            print(f"      the credentials endpoint says {e.code} {e.read().decode(errors='replace')[:100]} -- "
                  "a new role alias takes a few minutes; asking again in 15 s", flush=True)
            time.sleep(15)
    return boto3.Session(aws_access_key_id=c["accessKeyId"], aws_secret_access_key=c["secretAccessKey"],
                         aws_session_token=c["sessionToken"], region_name=config.AWS_REGION)


def record(number, what, expect, got, detail=""):
    ok = got == expect
    results.append({"n": number, "what": what, "expect": expect, "got": got, "ok": ok, "detail": detail})
    print(f"{'  ok ' if ok else 'DIFF '} {number:>2}  {what:<58} expected {expect:<8} got {got:<8} {detail}", flush=True)


def check(number, what, expect, call):
    """allowed / denied / error, from what AWS answered."""
    try:
        out = call()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "?")
        message = (e.response.get("Error", {}).get("Message") or "")[:110]      # iot-data sends none (#95)
        if code == "ResourceInUseException":          # made by an earlier run: authorization came first
            record(number, what, expect, "allowed", "exists already, from an earlier run")
            return None
        record(number, what, expect, "denied" if code in DENIED else "error", f"{code}: {message}")
        return None
    record(number, what, expect, "allowed")
    return out


def putmedia(number, stream, expect):
    """A short test picture through kvssink, as the spike Thing. Whether its fragments landed is
    verify's to say; here: how the pipeline ended, and the lines that name a refusal."""
    if not (SDK / "build" / "libgstkvssink.so").exists():
        record(number, f"kvssink PutMedia to {stream}", expect, "skipped", f"no KVS plugin under {SDK}")
        return
    sink = (f"iot-certificate=iot-certificate,endpoint={config.IOT_CRED_ENDPOINT},cert-path={FOLDER}/adapter.cert.pem,"
            f"key-path={FOLDER}/adapter.private.key,ca-path={FOLDER}/cacert.pem,role-aliases={ALIAS},iot-thing-name={THING}")
    pipeline = ["timeout", "60", "gst-launch-1.0", "-e", "videotestsrc", "is-live=true", "num-buffers=225", "!",
                "video/x-raw,width=640,height=360,framerate=15/1", "!", "videoconvert", "!",
                "x264enc", "tune=zerolatency", "speed-preset=ultrafast", "key-int-max=15", "bframes=0", "!",
                "h264parse", "!", "video/x-h264,stream-format=avc,alignment=au", "!",
                "kvssink", f"stream-name={stream}", f"aws-region={config.AWS_REGION}", sink]
    env = dict(os.environ, GST_PLUGIN_PATH=str(SDK / "build"), LD_LIBRARY_PATH=str(SDK / "open-source" / "local" / "lib"))
    run = subprocess.run(pipeline, env=env, capture_output=True, text=True)
    out = run.stdout + run.stderr
    (FOLDER / f"kvssink-{stream}.log").write_text(out)
    got, detail = kvssink_outcome(out)
    record(number, f"kvssink PutMedia to {stream} (fragments: see verify)", expect, got, f"exit {run.returncode}; {detail}")


def kvssink_outcome(log):
    """("denied" | "sent" | "unclear", why) from kvssink's log, in the SDK's own words. Never a bare "403":
    the thread ID and timestamps in every line hold such digits, which first made two good sends look
    refused (FoundAndFixed.md #95)."""
    lines = log.splitlines()
    refused = [ln for ln in lines if "HTTP Error 403" in ln or "Unable to create Kinesis Video stream" in ln]
    if refused:
        return "denied", refused[0].split("] ", 1)[-1][:110]
    if any("persisted ack" in ln.lower() for ln in lines):
        return "sent", "persisted ACKs in its log"
    return "unclear", "neither a refusal nor an ACK in its log"


def main():
    if not (FOLDER / "adapter.cert.pem").exists():
        sys.exit(f"no spike identity in {FOLDER}: bash cloud/spike-d13.sh setup first")
    print(f"the spike Thing {THING} asks for its own resources and for {OTHER}'s, through {ALIAS}\n")

    try:
        s = credentials(THING, wait=300)
    except urllib.error.HTTPError as e:
        sys.exit(f"the credentials endpoint refused the spike Thing: HTTP {e.code} {e.read().decode(errors='replace')[:200]}")
    record(1, "credentials with the Thing's own name", "issued", "issued")
    try:
        credentials(OTHER)
        record(2, "credentials claiming another Thing's name", "refused", "issued")
    except urllib.error.HTTPError as e:
        record(2, "credentials claiming another Thing's name", "refused", "refused", f"HTTP {e.code}")

    ssm = s.client("ssm")
    check(3, "SSM: read own camera secret", "allowed",
          lambda: ssm.get_parameter(Name=f"/vsz-spike/adapters/{THING}/cameras/cam-90", WithDecryption=True))
    check(4, "SSM: read the other adapter's secret", "denied",
          lambda: ssm.get_parameter(Name=f"/vsz-spike/adapters/{OTHER}/cameras/cam-91", WithDecryption=True))
    check(5, "SSM: write own camera secret", "allowed",
          lambda: ssm.put_parameter(Name=f"/vsz-spike/adapters/{THING}/cameras/cam-92", Value="spike", Type="SecureString",
                                    Overwrite=True))
    check(6, "SSM: write under the other adapter", "denied",
          lambda: ssm.put_parameter(Name=f"/vsz-spike/adapters/{OTHER}/cameras/cam-93", Value="spike", Type="SecureString",
                                    Overwrite=True))

    kv = s.client("kinesisvideo")
    check(7, "KVS by tag: describe own stream", "allowed", lambda: kv.describe_stream(StreamName="vsz-spike-tag-a"))
    check(8, "KVS by tag: describe the other's stream", "denied", lambda: kv.describe_stream(StreamName="vsz-spike-tag-b"))
    check(9, "KVS by tag: PutMedia endpoint of own stream", "allowed",
          lambda: kv.get_data_endpoint(StreamName="vsz-spike-tag-a", APIName="PUT_MEDIA"))
    check(10, "KVS by tag: PutMedia endpoint of the other's", "denied",
          lambda: kv.get_data_endpoint(StreamName="vsz-spike-tag-b", APIName="PUT_MEDIA"))
    check(11, "KVS by tag: create a stream tagged as own", "allowed",
          lambda: kv.create_stream(StreamName="vsz-spike-tag-a2", DataRetentionInHours=1, MediaType="video/h264",
                                   Tags={"adapterId": THING}))
    check(12, "KVS by tag: create a stream tagged as the other's", "denied",
          lambda: kv.create_stream(StreamName="vsz-spike-tag-b2", DataRetentionInHours=1, Tags={"adapterId": OTHER}))
    check(13, "KVS by tag: create a stream with no tag", "denied",
          lambda: kv.create_stream(StreamName="vsz-spike-tag-a3", DataRetentionInHours=1))
    check(14, "KVS by tag: re-tag the other's stream as own", "denied",
          lambda: kv.tag_stream(StreamName="vsz-spike-tag-b", Tags={"adapterId": THING}))
    check(15, "KVS by name: describe own (vsz-spike-a.*)", "allowed", lambda: kv.describe_stream(StreamName=f"{THING}.name"))
    check(16, "KVS by name: describe the other's (vsz-spike-b.*)", "denied",
          lambda: kv.describe_stream(StreamName=f"{OTHER}.name"))
    putmedia(17, "vsz-spike-tag-a", "sent")
    putmedia(18, "vsz-spike-tag-b", "denied")
    putmedia(19, f"{THING}.name", "sent")

    db = s.client("dynamodb")
    got = check(20, "DynamoDB: query own rows through the adapter index", "allowed",
                lambda: db.query(TableName=TABLE, IndexName=INDEX, KeyConditionExpression="adapterId = :a",
                                 ExpressionAttributeValues={":a": {"S": THING}}))
    if got is not None:
        ids = sorted(i["cameraId"]["S"] for i in got.get("Items", []))
        record(21, "DynamoDB: ...and they are its own rows only", "cam-90", ",".join(ids) or "none")
    check(22, "DynamoDB: query the other's rows through the index", "denied",
          lambda: db.query(TableName=TABLE, IndexName=INDEX, KeyConditionExpression="adapterId = :a",
                           ExpressionAttributeValues={":a": {"S": OTHER}}))
    check(23, "DynamoDB: scan the whole table", "denied", lambda: db.scan(TableName=TABLE))
    check(24, "DynamoDB: read a row by its key on the table", "denied",
          lambda: db.get_item(TableName=TABLE, Key={"cameraId": {"S": "cam-91"}}))

    s3 = s.client("s3")
    check(25, "S3: put under own prefix", "allowed",
          lambda: s3.put_object(Bucket=BUCKET, Key=f"thumbs/{THING}/cam-90.jpg", Body=b"spike"))
    check(26, "S3: put under the other's prefix", "denied",
          lambda: s3.put_object(Bucket=BUCKET, Key=f"thumbs/{OTHER}/cam-91.jpg", Body=b"spike"))

    iot = s.client("iot-data")
    check(27, "IoT: publish to own topic", "allowed",
          lambda: iot.publish(topic=f"vsz-spike/{THING}/event", qos=0, payload=b"{}"))
    check(28, "IoT: publish to the other's topic", "denied",
          lambda: iot.publish(topic=f"vsz-spike/{OTHER}/event", qos=0, payload=b"{}"))

    try:
        bare = credentials(None)
        record(29, "credentials without the Thing name header", "issued", "issued")
        check(30, "...then reading own secret: the variable is unset", "denied",
              lambda: bare.client("ssm").get_parameter(Name=f"/vsz-spike/adapters/{THING}/cameras/cam-90"))
    except urllib.error.HTTPError as e:
        record(29, "credentials without the Thing name header", "issued", "refused", f"HTTP {e.code}")

    (FOLDER / "results.json").write_text(json.dumps(results, indent=2))
    diffs = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(diffs)} of {len(results)} as expected; results in {FOLDER / 'results.json'}")
    print("next: bash cloud/spike-d13.sh verify   (with your login: which streams received fragments)")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
