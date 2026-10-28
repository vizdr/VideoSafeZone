"""Stand-ins for the things the code talks to (PYTEST.md, Phase 2).

Each fake is installed on the one module under test with monkeypatch, never globally, and is just
large enough for the code here. Anything it doesn't model fails loudly rather than answering
something plausible.

    ModuleShim     a module as one module sees it: a few attributes replaced, the rest real
    FakeClock      time.monotonic()/sleep() that only move when told to
    FakeTable      a DynamoDB Table resource: get/put/update/delete, scan and query with pages,
                   SET updates and simple conditions (=, attribute_[not_]exists, AND)
    FakeSession    what aws_device_creds.get_session() returns: .resource("dynamodb").Table(n)
    FakeBoto3      the `boto3` a Lambda sees: tables from FakeTable, clients stubbed with
                   botocore's own Stubber (real parameter validation, no network)
    FakeMediaMTX   MediaMTX's control API, as the `requests` module a module sees
    invoke         calls an API Lambda's handler and checks its CORS header
"""
import json
import re
from copy import deepcopy

import boto3
import requests
from botocore.exceptions import ClientError
from botocore.stub import Stubber

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

    def scan(self, ExclusiveStartKey=None, Limit=None, **kw):
        if kw:
            raise NotImplementedError(f"FakeTable.scan: {sorted(kw)}")
        return self._page(self.items, ExclusiveStartKey, Limit)

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
        resp = self._page(rows, ExclusiveStartKey, Limit)
        if ProjectionExpression:
            keep = [a.strip() for a in ProjectionExpression.split(",")]
            resp["Items"] = [{a: i[a] for a in keep if a in i} for i in resp["Items"]]
        return resp

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
        m = re.fullmatch(r"SET\s+(.+)", UpdateExpression.strip())
        if not m:
            raise NotImplementedError(f"FakeTable.update_item: {UpdateExpression}")
        if row is None:                      # DynamoDB creates the item
            row = dict(Key)
            self.items.append(row)
        for part in m.group(1).split(","):
            name, placeholder = (x.strip() for x in part.split("="))
            row[names.get(name, name)] = deepcopy(values[placeholder])

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


class FakeSession:
    """aws_device_creds.get_session()'s result: monkeypatch.setattr(aws_device_creds,
    "get_session", lambda *a, **k: FakeSession(cameras=table))."""

    def __init__(self, **tables):
        self.tables = tables

    def resource(self, service, **kw):
        assert service == "dynamodb", service
        return _FakeDynamoDB(self.tables)


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
      down       every call raises ConnectionError, as when MediaMTX is stopped

    `calls` records (METHOD, route, json body). POST on the delete route answers 404, as MediaMTX
    does: that is how a rollback once left orphan paths behind (FoundAndFixed.md #36).
    """

    def __init__(self, conf=None, state=None, snapshots=None, down=False):
        self.conf = deepcopy(conf or {})
        self.state = deepcopy(state or {})
        self.snapshots = list(snapshots or [])
        self.down = down
        self.calls = []

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
            items = self.snapshots.pop(0) if len(self.snapshots) > 1 else \
                (self.snapshots[0] if self.snapshots else [])
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
            return _Response(200, {})
        if method == "PATCH" and verb == "patch":
            if path not in self.conf:
                return _Response(404, _NOT_FOUND)
            self.conf[path].update(deepcopy(body))
            return _Response(200, {})
        if method == "DELETE" and verb == "delete":
            if path not in self.conf:
                return _Response(404, _NOT_FOUND)
            del self.conf[path]
            return _Response(200, {})
        return _Response(404, None, text="404 page not found")

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

