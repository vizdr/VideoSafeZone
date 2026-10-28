"""config (deployment identity) and aws_state (the agent's heartbeat file) (PYTEST.md, step 5)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import aws_state
import config


def test_read_file(tmp_path):
    f = tmp_path / "adapter.env"
    f.write_text('# comment\n\nAWS_REGION=eu-central-1\nTHING_NAME = "adapter-01"\n'
                 "IOT_ROLE_ALIAS='Alias'\nNOT A PAIR\nURL=https://x/?a=b\n")
    assert config._read_file(f) == {"AWS_REGION": "eu-central-1", "THING_NAME": "adapter-01",
                                    "IOT_ROLE_ALIAS": "Alias", "URL": "https://x/?a=b"}


def test_read_missing_file_is_empty(tmp_path):
    assert config._read_file(tmp_path / "nope.env") == {}


def test_environment_overrides_the_file(monkeypatch):
    monkeypatch.setattr(config, "_FILE", {"THING_NAME": "from-file"})
    monkeypatch.setenv("THING_NAME", "from-env")
    assert config.get("THING_NAME") == "from-env"


def test_missing_key_is_an_error_naming_key_and_file(monkeypatch):
    # No silent default: a stale default would talk to whichever account it points at.
    monkeypatch.setattr(config, "_FILE", {})
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)
    with pytest.raises(config.ConfigError, match="EVIDENCE_BUCKET.*adapter.env"):
        config.get("EVIDENCE_BUCKET")


def test_empty_value_counts_as_missing(monkeypatch):
    monkeypatch.setattr(config, "_FILE", {"THING_NAME": ""})
    monkeypatch.delenv("THING_NAME", raising=False)
    with pytest.raises(config.ConfigError):
        config.get("THING_NAME")


# --- aws_state ------------------------------------------------------------------------------------

@pytest.mark.parametrize("online", [True, False])
def test_heartbeat_round_trip(online):
    aws_state.write_state(online, since="2026-10-25T12:00:00+00:00", seq=7)
    st = aws_state.read_state()
    assert (st["online"], st["stale"], st["since"], st["seq"]) == (online, False,
                                                                   "2026-10-25T12:00:00+00:00", 7)


def test_write_leaves_no_temp_files():
    aws_state.write_state(True)
    assert [p.name for p in aws_state.STATE_FILE.parent.iterdir()] == ["aws-state.json"]


def test_stale_heartbeat_is_unknown_not_offline():
    # A dead agent must read as "don't know", never as an outage -- or a crashed agent would make
    # the outage buffer record forever.
    old = datetime.now(timezone.utc) - timedelta(seconds=aws_state.STALE_AFTER_SEC + 5)
    aws_state.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    aws_state.STATE_FILE.write_text(json.dumps({"online": False, "ts": old.isoformat()}))
    st = aws_state.read_state()
    assert st["online"] is None and st["stale"] is True


@pytest.mark.parametrize("content", [None, "", "{not json", '{"online": true}'])
def test_missing_or_unreadable_file_is_unknown(content):
    if content is not None:
        aws_state.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        aws_state.STATE_FILE.write_text(content)
    st = aws_state.read_state()
    assert st["online"] is None and st["stale"] is True
