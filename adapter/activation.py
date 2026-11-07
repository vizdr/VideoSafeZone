"""Whether this adapter is activated (DEALER-PANEL-PLAN.md Phase 6.3, D15, D16): agent.py writes it,
the admin GUI reads it before registering a camera.

The cloud says so in a retained MQTT message on adapter/<thing>/config -- {"activated": true|false}
-- published when the adapter is made, activated with its Cloud ID, released or revoked. agent.py
holds the only MQTT connection this Thing may have, so it receives the message, at every connect,
and writes it here; a small file is the whole IPC, as aws_state's.

On tmpfs, like aws_state: after a reboot nothing is known until the agent has connected, and
unknown refuses as "not activated" does. A camera registered on an adapter that no customer has
activated would be nobody's -- no token reaches it (D4) -- so the admin GUI says why instead.
This gate is a convenience on the Pi; what a customer may see is decided in the cloud.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "vms"


STATE_FILE = _runtime_dir() / "activation.json"


def save(payload) -> dict:
    """The config message as received (bytes or text), checked and written atomically. Raises
    ValueError for a message that isn't {"activated": true|false, ...}."""
    try:
        msg = json.loads(payload)
    except (TypeError, ValueError) as e:
        raise ValueError(f"not JSON: {e}") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("activated"), bool):
        raise ValueError('expected {"activated": true|false}')
    state = {"activated": msg["activated"], "at": msg.get("at"),
             "received": datetime.now(timezone.utc).isoformat()}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_FILE.parent, prefix=".activation-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return state


def load():
    """What the agent last wrote, or None (never heard, or unreadable)."""
    try:
        state = json.loads(Path(STATE_FILE).read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and isinstance(state.get("activated"), bool) else None


def registration_refusal():
    """Why a new camera can't be registered here now, or None when it can."""
    state = load()
    if state is None:
        return ("this adapter hasn't heard from the cloud whether it is activated yet -- "
                "is kvs-agent running and online?")
    if not state["activated"]:
        return ("this adapter isn't activated: activate it with its Cloud ID on the web page first "
                "(Add adapter), then register cameras")
    return None
