"""The narrowed device role (DEALER-PANEL-PLAN.md D13, Phase 10.2f): the files provision.sh narrow puts.

Every statement an adapter keeps is scoped by its own Thing -- in the resource, or by the stream's tag --
and the lists in provision.sh name exactly these files, never a kept policy among the dropped ones.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SH = (ROOT / "cloud/provision.sh").read_text()
THING = "${credentials-iot:ThingName}"


def listed(name):
    return dict(p.split(":") for p in re.search(rf'^{name}="([^"]*)"', SH, re.M).group(1).split())


def test_every_kept_statement_is_scoped_by_the_callers_thing():
    files = {**listed("NARROW_ADD"), **listed("NARROW_KEEP")}
    assert set(files) == {"streams", "uploads", "events", "own-rows", "registry-route", "camera-secrets"}
    for name, file in files.items():
        for st in json.loads((ROOT / "cloud/iam" / file).read_text())["Statement"]:
            resources = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
            scoped = all(THING in r for r in resources) or THING in json.dumps(st.get("Condition", {}))
            assert st["Effect"] == "Allow" and scoped, (name, st.get("Sid"))
            assert "*" not in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]]), (name, st.get("Sid"))


def test_streams_are_reached_by_their_tag_and_made_only_so_tagged():
    sts = {s["Sid"]: s for s in json.loads((ROOT / "cloud/iam/kvs-adapter-streams-policy.json").read_text())["Statement"]}
    assert sts["OwnStreamsByTag"]["Condition"] == {"StringEquals": {"aws:ResourceTag/adapterId": THING}}
    create = sts["NewStreamsOnlyTaggedAsOwn"]["Condition"]
    assert create["StringEquals"] == {"aws:RequestTag/adapterId": THING}
    assert create["ForAllValues:StringEquals"] == {"aws:TagKeys": ["adapterId"]}
    assert sts["TagOnlyOwnStreamsAsOwn"]["Condition"]["StringEquals"] == \
        {"aws:RequestTag/adapterId": THING, "aws:ResourceTag/adapterId": THING}


def test_the_wide_policies_go_and_nothing_kept_is_dropped():
    drop = set(re.search(r'^NARROW_DROP="([^"]*)"', SH, re.M).group(1).split())
    assert drop == {"KVSProducer", "OutageBackfill", "Thumbnails", "camera-requests", "PirLocal"}
    assert not drop & {*listed("NARROW_ADD"), *listed("NARROW_KEEP")}


def test_the_narrowing_refuses_without_vms_moved():
    body = SH[SH.index("provision_narrow() {"):SH.index("provision_narrow_rollback() {")]
    assert 'die "$fails condition(s) not met -- nothing was changed"' in body
    ready = SH[SH.index("narrow_ready() {"):SH.index("provision_narrow() {")]
    assert 'if [ "$VMS_MOVED" -eq 1 ]' in ready
    assert body.index("backup=") < body.index("put-role-policy") < body.index("delete-role-policy")    # saved first
