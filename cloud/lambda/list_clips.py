import boto3, os, json
from datetime import datetime

REGION = os.environ["AWS_REGION"]
BUCKET = os.environ["BUCKET"]
DEFAULT_CAMERA = os.environ.get("STREAM_NAME", "cam-01")
# A caller that doesn't ask for a page size gets what this route always returned. The
# base project VMS calls this same function without `limit` (FoundAndFixed.md #48).
DEFAULT_LIMIT = MAX_LIMIT = 50

CORS = {"Access-Control-Allow-Origin": "*"}
cameras_table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")

def bad_request(msg):
    return {"statusCode": 400, "headers": CORS, "body": json.dumps({"error": msg})}

def lambda_handler(event, context):
    try:
        params = event.get("queryStringParameters") or {}
        camera = params.get("camera", DEFAULT_CAMERA)
        if "Item" not in cameras_table.get_item(Key={"cameraId": camera}):
            return bad_request(f"unknown camera '{camera}'")

        # Paging (FoundAndFixed.md #51): `limit` clips per page, newest first; `before` is
        # the `next` value of the previous page -- the startTs of its last clip.
        try:
            limit = int(params.get("limit", DEFAULT_LIMIT))
        except ValueError:
            return bad_request("limit must be an integer")
        if not 1 <= limit <= MAX_LIMIT:
            return bad_request(f"limit must be between 1 and {MAX_LIMIT}")
        before = params.get("before")
        if before is not None:
            try:
                datetime.fromisoformat(before)
            except ValueError:
                return bad_request("before must be an ISO timestamp")

        table = boto3.resource("dynamodb", region_name=REGION).Table("clips")
        # newest first -- ScanIndexForward=False on Query (not Scan) needs the sort key.
        # One item more than the page, to know whether an older page exists: DynamoDB's own
        # LastEvaluatedKey is also set when the page ends exactly at the last clip, which
        # would offer an "Older" page that turns out empty.
        query = dict(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("cameraId").eq(camera),
            ScanIndexForward=False,
            Limit=limit + 1,
        )
        if before is not None:
            # Built here, not taken from the client, so a cursor can't reach another
            # camera's clips. The key need not still exist (its clip may have been deleted).
            query["ExclusiveStartKey"] = {"cameraId": camera, "startTs": before}
        items = table.query(**query).get("Items", [])
        page = items[:limit]
        next_cursor = page[-1]["startTs"] if len(items) > limit else None

        # One head_object per clip to report its live storage tier -- fine at demo scale
        # (one page of clips), would need a different design (store tier in DynamoDB,
        # updated by an S3 Lifecycle -> EventBridge rule) before pages get large.
        s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com")
        clips = []
        for i in page:
            key = i["s3Key"]
            try:
                head = s3.head_object(Bucket=BUCKET, Key=key)
                tier = head.get("StorageClass", "STANDARD")
                restoring = 'ongoing-request="true"' in (head.get("Restore") or "")
            except Exception:
                tier, restoring = "UNKNOWN", False
            clips.append({
                "startTs": i["startTs"], "s3Key": key, "labels": i.get("labels", []),
                "tier": tier, "restoring": restoring,
                # DynamoDB's resource layer returns Decimal, which json.dumps can't
                # serialize directly -- cast to int. None for clips recorded before this
                # field existed (older items simply don't have the attribute).
                "durationSec": int(i["durationSec"]) if "durationSec" in i else None,
                # "h264" / "h265", read from the clip itself when it was made
                # (record_clip.py, clip_to_s3.py, adapter/outage_uploader.py). Shown after
                # the duration; None only for a clip whose codec could not be read.
                "videoCodec": i.get("videoCodec"),
            })
        return {"statusCode": 200, "headers": CORS,
                "body": json.dumps({"clips": clips, "next": next_cursor})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
