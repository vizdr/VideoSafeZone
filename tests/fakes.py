"""Stand-ins for the things the code talks to (PYTEST.md, Phase 2).

Each fake is installed on the one module under test with monkeypatch, never globally, and is just
large enough for the code here. Anything it doesn't model fails loudly rather than answering
something plausible.

    ModuleShim     a module as one module sees it: a few attributes replaced, the rest real
    FakeClock      time.monotonic()/sleep() that only move when told to
    FakeTable      a DynamoDB Table resource: get/put/update/delete, scan and query with pages,
                   SET/REMOVE updates and simple conditions (=, attribute_[not_]exists, AND)
    FakeBoto3      the `boto3` a Lambda sees, and the session aws_device_creds.get_session()
                   returns on the Pi: tables from FakeTable, clients stubbed with botocore's own
                   Stubber (real parameter validation, no network)
    FakeMediaMTX   MediaMTX's control API, as the `requests` module a module sees
    FakeMedia2     an ONVIF camera's Media2 service, as onvif_media2.Media2 presents it
    encoder_xml    a Media2 video encoder configuration (lxml element)
    invoke         calls an API Lambda's handler and checks its CORS header
"""
import json
import re
from copy import deepcopy

import boto3
import requests
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from lxml import etree

REGION = "eu-central-1"


class ModuleShim:
    """`real` with some attributes replaced: monkeypatch.setattr(mod, "time", ModuleShim(time,
    monotonic=clock.monotonic)) changes what `mod` sees and nothing else, pytest included."""

    def __init__(self, real, **overrides):
        self._real = real
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._real, name)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    advance = sleep


# --- DynamoDB ---------------------------------------------------------------------------------

def _conditional_check_failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException",
                                  "Message": "The conditional request failed"}}, "UpdateItem")


class FakeTable:
    """A DynamoDB Table. `page_size` splits scans and queries into pages, as DynamoDB's 1 MB limit
    does, so code that forgets LastEvaluatedKey sees only the first page. Every write is appended
    to `writes` as (operation, arguments)."""

    def __init__(self, items=(), hash_key="cameraId", range_key=None, page_size=None):
        self.hash_key, self.range_key = hash_key, range_key
        self.items = [deepcopy(i) for i in items]
        self.page_size = page_size
        self.writes = []

    # -- helpers for tests
    def row(self, *key):
        names = [k for k in (self.hash_key, self.range_key) if k]
        return next((i for i in self.items if tuple(i.get(n) for n in names) == key), None)

    def _key_of(self, item):
        return {k: item[k] for k in (self.hash_key, self.range_key) if k}

    def _index(self, key):
        return next((n for n, i in enumerate(self.items) if self._key_of(i) == key), None)

    # -- reads
    def get_item(self, Key, **kw):
        n = self._index(Key)
        return {"Item": deepcopy(self.items[n])} if n is not None else {}

    def _page(self, rows, ExclusiveStartKey=None, Limit=None):
        start = 0
        if ExclusiveStartKey is not None:
            keys = [self._key_of(r) for r in rows]
            start = keys.index(ExclusiveStartKey) + 1
        size = min(x for x in (Limit, self.page_size, len(rows) - start) if x is not None)
        page = rows[start:start + size]
        resp = {"Items": deepcopy(page), "Count": len(page)}
        if start + size < len(rows):
            resp["LastEvaluatedKey"] = self._key_of(page[-1])
        return resp

    def scan(self, ExclusiveStartKey=None, Limit=None, ProjectionExpression=None, **kw):
        if kw:
            raise NotImplementedError(f"FakeTable.scan: {sorted(kw)}")
        return self._project(self._page(self.items, ExclusiveStartKey, Limit), ProjectionExpression)

    @staticmethod
    def _project(resp, projection):
        if projection:
            keep = [a.strip() for a in projection.split(",")]
            resp["Items"] = [{a: i[a] for a in keep if a in i} for i in resp["Items"]]
        return resp

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None,
              ExclusiveStartKey=None, ProjectionExpression=None, **kw):
        if kw:
            raise NotImplementedError(f"FakeTable.query: {sorted(kw)}")
        expr = KeyConditionExpression.get_expression()
        key, value = expr["values"]
        if expr["operator"] != "=" or key.name != self.hash_key:
            raise NotImplementedError(f"FakeTable.query: {expr}")
        rows = sorted((i for i in self.items if i.get(self.hash_key) == value),
                      key=lambda i: i[self.range_key], reverse=not ScanIndexForward)
        return self._project(self._page(rows, ExclusiveStartKey, Limit), ProjectionExpression)

    # -- writes
    def put_item(self, Item, **kw):
        self.writes.append(("put", deepcopy(Item)))
        n = self._index(self._key_of(Item))
        if n is None:
            self.items.append(deepcopy(Item))
        else:
            self.items[n] = deepcopy(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None,
                    ExpressionAttributeNames=None, ConditionExpression=None, **kw):
        if kw:
            raise NotImplementedError(f"FakeTable.update_item: {sorted(kw)}")
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        self.writes.append(("update", {"Key": Key, "UpdateExpression": UpdateExpression,
                                       "ExpressionAttributeValues": deepcopy(values),
                                       "ExpressionAttributeNames": names,
                                       "ConditionExpression": ConditionExpression}))
        n = self._index(Key)
        row = self.items[n] if n is not None else None
        if ConditionExpression and not self._holds(ConditionExpression, row, names, values):
            raise _conditional_check_failed()
        m = re.fullmatch(r"(?:SET\s+(?P<set>.+?))?\s*(?:REMOVE\s+(?P<remove>.+))?",
                         UpdateExpression.strip())
        if not m or not (m.group("set") or m.group("remove")):
            raise NotImplementedError(f"FakeTable.update_item: {UpdateExpression}")
        if row is None:                      # DynamoDB creates the item
            row = dict(Key)
            self.items.append(row)
        for part in (m.group("set") or "").split(",") if m.group("set") else []:
            name, placeholder = (x.strip() for x in part.split("="))
            row[names.get(name, name)] = deepcopy(values[placeholder])
        for name in (m.group("remove") or "").split(",") if m.group("remove") else []:
            row.pop(names.get(name.strip(), name.strip()), None)     # absent: a no-op, as in DynamoDB

    def delete_item(self, Key, **kw):
        self.writes.append(("delete", dict(Key)))
        n = self._index(Key)
        if n is not None:
            del self.items[n]

    @staticmethod
    def _holds(condition, row, names, values):
        for term in re.split(r"\s+AND\s+", condition.strip()):
            if m := re.fullmatch(r"attribute_(not_)?exists\((\S+)\)", term):
                present = row is not None and names.get(m.group(2), m.group(2)) in row
                if present == bool(m.group(1)):
                    return False
            elif m := re.fullmatch(r"(\S+)\s*=\s*(:\w+)", term):
                if row is None or row.get(names.get(m.group(1), m.group(1))) != values[m.group(2)]:
                    return False
            else:
                raise NotImplementedError(f"FakeTable condition: {term}")
        return True


class _FakeDynamoDB:
    def __init__(self, tables):
        self._tables = tables

    def Table(self, name):  # noqa: N802 -- boto3's name
        return self._tables[name]


class FakeBoto3:
    """The `boto3` a Lambda module sees. Tables are FakeTables; a client is a real botocore client
    with a Stubber activated on it, so each call is checked against the service model and answered
    from what the test queued, never sent. Construct with the tables, then queue responses on
    .stub("s3") etc. verify() fails a test that left a queued response unused."""

    def __init__(self, **tables):
        self.tables = tables
        self._clients, self._stubs, self._built_with = {}, {}, {}
        self.client_kwargs = {}           # service -> kwargs of the Lambda's last client() call

    def _build(self, service, kw):
        client = boto3.client(service, **{"region_name": REGION, **kw})
        stub = Stubber(client)
        if service in self._stubs:        # keep what the test queued (Stubber keeps it in _queue)
            stub._queue = self._stubs[service]._queue
            self._stubs[service].deactivate()
        stub.activate()
        self._clients[service], self._stubs[service], self._built_with[service] = client, stub, kw

    def stub(self, service) -> Stubber:
        if service not in self._stubs:
            self._build(service, {})
        return self._stubs[service]

    def client(self, service, **kw):
        """The stubbed client, built with the Lambda's own arguments (endpoint_url, region), so
        what depends on them -- a presigned URL's host -- is the Lambda's doing, not the fake's."""
        self.client_kwargs[service] = kw
        if self._built_with.get(service) != kw:
            self._build(service, kw)
        return self._clients[service]

    def resource(self, service, **kw):
        assert service == "dynamodb", service
        return _FakeDynamoDB(self.tables)

    def verify(self):
        for stub in self._stubs.values():
            stub.assert_no_pending_responses()


def invoke(handler_module, body=None, query=None):
    """Call an API Gateway Lambda. Returns (statusCode, decoded body). Every answer, errors
    included, must carry the CORS header: without it the browser can't read the response at all
    and fetch() throws a bare NetworkError (FoundAndFixed.md #8)."""
    event = {}
    if body is not None:
        event["body"] = json.dumps(body)
    if query is not None:
        event["queryStringParameters"] = query
    resp = handler_module.lambda_handler(event, None)
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*", resp
    return resp["statusCode"], json.loads(resp["body"])


# --- MediaMTX ---------------------------------------------------------------------------------

class _Response:
    def __init__(self, status_code, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.ok = status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}")


_NOT_FOUND = {"status": "error", "error": "path not found"}
API = "http://127.0.0.1:9997"


class FakeMediaMTX:
    """MediaMTX's control API, standing in for the `requests` module of one module:
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(conf={...})).

      conf       path -> configuration (/v3/config/paths/...); changed by add/patch/delete
      state      path -> runtime state (/v3/paths/get/<path>)
      snapshots  successive answers of /v3/paths/list, as lists of path dicts
      flowing    paths whose bytes keep growing: without `snapshots`, /v3/paths/list is built
                 from `state`, and each call adds bytes to these paths only (a stalled path
                 stays `ready` with a constant count, as in #50)
      down       every call raises ConnectionError, as when MediaMTX is stopped
      on_write   called as on_write(method, path, body) after each successful write, for a test
                 to move the wire as a camera would (a new source, a new codec)

    `calls` records (METHOD, route, json body). POST on the delete route answers 404, as MediaMTX
    does: that is how a rollback once left orphan paths behind (FoundAndFixed.md #36).
    """

    def __init__(self, conf=None, state=None, snapshots=None, down=False, flowing=(), on_write=None):
        self.conf = deepcopy(conf or {})
        self.state = deepcopy(state or {})
        self.snapshots = list(snapshots or [])
        self.down = down
        self.flowing = set(flowing)
        self.on_write = on_write
        self.calls = []
        self._ticks = 0

    def __getattr__(self, name):          # RequestException, ConnectionError, ...
        return getattr(requests, name)

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def _route(self, method, url, body=None):
        assert url.startswith(API), url
        route = url[len(API):]
        self.calls.append((method, route, deepcopy(body)))
        if self.down:
            raise requests.ConnectionError("connection refused")
        m = re.fullmatch(r"/v3/(config/paths/(get|add|patch|delete)|paths/get)/(.+)", route)
        verb, path = (m.group(2) or "state", m.group(3)) if m else (None, None)
        if method == "GET" and route == "/v3/paths/list":
            if self.snapshots:
                items = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
            else:
                self._ticks += 1
                items = [ready_path(p, 1000 * (self._ticks if p in self.flowing else 1),
                                    bool(s.get("ready"))) for p, s in self.state.items()]
            return _Response(200, {"itemCount": len(items), "items": deepcopy(items)})
        if method == "GET" and route == "/v3/config/global/get":
            return _Response(200, {})
        if method == "GET" and verb == "get":
            return _Response(200, deepcopy(self.conf[path])) if path in self.conf \
                else _Response(404, _NOT_FOUND)
        if method == "GET" and verb == "state":
            return _Response(200, deepcopy(self.state[path])) if path in self.state \
                else _Response(404, _NOT_FOUND)
        if method == "POST" and verb == "add":
            if path in self.conf:
                return _Response(400, {"status": "error", "error": "path already exists"})
            self.conf[path] = deepcopy(body)
            return self._wrote(method, path, body)
        if method == "PATCH" and verb == "patch":
            if path not in self.conf:
                return _Response(404, _NOT_FOUND)
            self.conf[path].update(deepcopy(body))
            return self._wrote(method, path, body)
        if method == "DELETE" and verb == "delete":
            if path not in self.conf:
                return _Response(404, _NOT_FOUND)
            del self.conf[path]
            return self._wrote(method, path, body)
        return _Response(404, None, text="404 page not found")

    def _wrote(self, method, path, body):
        if self.on_write:
            self.on_write(method, path, deepcopy(body))
        return _Response(200, {})

    def get(self, url, timeout=None, **kw):
        return self._route("GET", url)

    def post(self, url, json=None, timeout=None, **kw):
        return self._route("POST", url, json)

    def patch(self, url, json=None, timeout=None, **kw):
        return self._route("PATCH", url, json)

    def delete(self, url, timeout=None, **kw):
        return self._route("DELETE", url)


def ready_path(name, bytes_received, ready=True):
    """One item of /v3/paths/list."""
    return {"name": name, "ready": ready, "bytesReceived": bytes_received}


def path_state(codec="H264", width=1280, height=720, ready_time="2026-10-28T10:00:00Z", audio=None):
    """/v3/paths/get/<path> for a ready path carrying `codec` (and an audio track, if given)."""
    tracks2 = [{"codec": codec, "codecProps": {"width": width, "height": height}}]
    if audio:
        tracks2.append(audio)
    return {"ready": True, "readyTime": ready_time, "tracks": [t["codec"] for t in tracks2],
            "tracks2": tracks2}


# --- ONVIF Media2 -----------------------------------------------------------------------------

_NS = {"tr2": "http://www.onvif.org/ver20/media/wsdl", "tt": "http://www.onvif.org/ver10/schema"}


def encoder_xml(encoding="H264", width=1920, height=1080, fps="15", bitrate="4096", gov="30",
                profile="Main", token="VideoEncoderToken_1"):
    """A Media2 GetVideoEncoderConfigurations element, as onvif_media2.Media2.encoder_config()
    returns it. fps=None leaves out the frame-rate limit; gov/profile None leave out the attribute."""
    attrs = f'token="{token}"' + (f' GovLength="{gov}"' if gov else "") + \
        (f' Profile="{profile}"' if profile else "")
    rate = f"<tt:FrameRateLimit>{fps}</tt:FrameRateLimit>" if fps is not None else ""
    return etree.fromstring(
        f'<tr2:Configurations xmlns:tr2="{_NS["tr2"]}" xmlns:tt="{_NS["tt"]}" {attrs}>'
        "<tt:Name>VideoEncoder_1</tt:Name><tt:UseCount>1</tt:UseCount>"
        f"<tt:Encoding>{encoding}</tt:Encoding>"
        f"<tt:Resolution><tt:Width>{width}</tt:Width><tt:Height>{height}</tt:Height></tt:Resolution>"
        f'<tt:RateControl ConstantBitRate="false">{rate}'
        f"<tt:BitrateLimit>{bitrate}</tt:BitrateLimit></tt:RateControl>"
        "<tt:Quality>3</tt:Quality></tr2:Configurations>")


def encoder_options(*encodings, frame_rates=(5.0, 10.0, 15.0, 20.0, 25.0), sizes=((2560, 1440), (1920, 1080), (1280, 720), (640, 360))):
    """onvif_media2.Media2.encoder_options()'s answer for an encoder offering `encodings`."""
    return {e: {"profiles": ["Main", "High"] if e == "H264" else ["Main"], "govLength": [1, 400],
                "frameRates": list(frame_rates),
                "resolutions": [{"width": w, "height": h, "frameRate": None, "bitrate": None}
                                for w, h in sizes],
                "bitrate": [32.0, 16384.0]} for e in encodings}


class FakeMedia2:
    """An ONVIF camera's Media2 service, as onvif_media2.Media2 presents it: install with
    monkeypatch.setattr(onvif_media2, "for_camera", lambda item: cam).

    `streams` is a list of (profile token, encoder token, URI path, encoder_xml element);
    `options` maps an encoder token to its encoder_options(). set_encoder_config() is accepted and
    applied, recorded in `sets`, and passed to `on_set(cfg)` so a test can move the wire."""

    def __init__(self, host, streams, options, on_set=None):
        self.host = host
        self._profiles = [{"token": tok, "name": tok, "encoderToken": enc,
                           "streamUri": f"rtsp://{host}:554{path}"} for tok, enc, path, _ in streams]
        self.configs = {}
        for _, enc, _, cfg in streams:               # a configuration carries its own token
            self.configs[enc] = deepcopy(cfg)
            self.configs[enc].set("token", enc)
        self.options = options
        self.on_set = on_set
        self.sets = []

    def profiles(self):
        return deepcopy(self._profiles)

    def encoder_config(self, token):
        return deepcopy(self.configs[token])

    def encoder_options(self, token):
        return deepcopy(self.options[token])

    def set_encoder_config(self, cfg):
        token = cfg.get("token")
        self.configs[token] = deepcopy(cfg)
        self.sets.append(deepcopy(cfg))
        if self.on_set:
            self.on_set(cfg)

