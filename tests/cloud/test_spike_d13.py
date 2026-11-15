"""The D13 spike's device side (cloud/spike_d13.py, DEALER-PANEL-PLAN.md Phase 10.1): the three faults its
first live run found (FoundAndFixed.md #95). The spike itself ran against AWS; what is pinned here is how
the tool reads what AWS and kvssink answered.
"""
import io
import json
import urllib.error

import pytest
from botocore.exceptions import ClientError

from loaders import load_cloud_tool

spike = load_cloud_tool("spike_d13")

# kvssink's own lines (2026-11-15). Every line carries a thread ID; this one holds the digits 4-0-3.
GOOD = """2026-11-15 09:02:45 [547533140320] WARN - describeStreamResult(): [vsz-spike-tag-a] Retention period (1640261632) returned
2026-11-15 09:02:50 [547533140320] INFO - getStreamData(): [vsz-spike-tag-a] Indicating an EOS after last persisted ACK is received for stream upload handle 0
"""
REFUSED = """2026-11-15 09:03:00 [547533140320] DEBUG - setRequestHeader(): Appending header to request: X-Amz-Date -> 20261115T080300Z
2026-11-15 09:03:01 [547533140320] WARN - curlCompleteSync(): [vsz-spike-tag-b] HTTP Error 403 : Response: {"Message":"User: ... not authorized"}
2026-11-15 09:03:31 [547533140320] ERROR -  Unable to create Kinesis Video stream. vsz-spike-tag-b Error status: 0xf
"""


def test_ff95_kvssink_is_read_in_its_own_words_never_by_status_digits():
    assert spike.kvssink_outcome(GOOD) == ("sent", "persisted ACKs in its log")
    got, why = spike.kvssink_outcome(REFUSED)
    assert got == "denied" and why.startswith("WARN - curlCompleteSync(): [vsz-spike-tag-b] HTTP Error 403")
    assert spike.kvssink_outcome("2026-11-15 09:03:00 [547533140320] DEBUG - nothing yet\n")[0] == "unclear"


@pytest.fixture
def recorded(monkeypatch):
    monkeypatch.setattr(spike, "results", [])
    return spike.results


def test_ff95_an_error_without_a_message_is_still_a_refusal(recorded):
    def publish():                                     # iot-data's refusal carries no message at all
        raise ClientError({"Error": {"Code": "ForbiddenException", "Message": None}}, "Publish")
    spike.check(28, "IoT: publish to the other's topic", "denied", publish)
    assert (recorded[0]["got"], recorded[0]["ok"]) == ("denied", True)


def test_ff95_a_stream_an_earlier_run_made_was_allowed(recorded):
    def create():
        raise ClientError({"Error": {"Code": "ResourceInUseException", "Message": "exists"}}, "CreateStream")
    spike.check(11, "KVS by tag: create a stream tagged as own", "allowed", create)
    assert (recorded[0]["got"], recorded[0]["detail"]) == ("allowed", "exists already, from an earlier run")


class _NoTLS:
    def load_cert_chain(self, **kwargs):
        pass


def test_ff95_a_new_role_alias_is_waited_for_not_taken_as_a_refusal(monkeypatch):
    answers = [urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"{}")),
               urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"{}")),
               io.BytesIO(json.dumps({"credentials": {"accessKeyId": "AKIA", "secretAccessKey": "s",
                                                      "sessionToken": "t"}}).encode())]

    def urlopen(req, context=None, timeout=None):
        assert req.get_header("X-amzn-iot-thingname") == "vsz-spike-a"
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    slept = []
    monkeypatch.setattr(spike.ssl, "create_default_context", lambda cafile=None: _NoTLS())
    monkeypatch.setattr(spike.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(spike.time, "sleep", slept.append)
    session = spike.credentials("vsz-spike-a", wait=300)
    assert session.get_credentials().access_key == "AKIA" and slept == [15, 15]
    answers[:] = [urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"{}"))]
    with pytest.raises(urllib.error.HTTPError):                   # no wait: the 404 is the answer
        spike.credentials("vsz-spike-a")
