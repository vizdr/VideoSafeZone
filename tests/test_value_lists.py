"""The value lists the pages offer match what the Lambdas accept (WEB-PANEL-PLAN.md Phase 1).

A choice is duplicated wherever it is offered or checked: the outage-buffer durations live in the
Lambda, the adapter, the admin GUI's route and page, and the web panel (the old single-file cloud
page was one more copy until the cutover, WEB-PANEL-PLAN.md Phase 3). A copy
that drifts offers a value the Lambda refuses, or hides one it allows. These read every copy from
source -- nothing is imported or run -- and compare it with the Lambda's.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "client-app" / "src" / "cameras" / "settings.ts"


def py_constant(path: Path, name: str, within: str | None = None):
    """The literal assigned to `name` in a Python file -- anywhere, or inside function `within`."""
    tree = ast.parse(path.read_text())
    if within:
        tree = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == within)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"no {name} in {path.relative_to(ROOT)}")


def ts_block(path: Path, name: str) -> str:
    """The array literal after `export const NAME` in a TypeScript file."""
    m = re.search(rf"export const {name}\b[^=]*=\s*\[(.*?)\];", path.read_text(), re.S)
    assert m, f"no {name} in {path.relative_to(ROOT)}"
    return m.group(1)


ADMIN_OUTAGE_ROUTE = "set_outage_buffer"   # app.py has another VALID, for the modes
LIMITS = py_constant(ROOT / "cloud" / "lambda" / "set_camera_outage_buffer.py", "VALID_LIMITS")


def test_outage_buffer_limits_match_the_lambda_everywhere():
    admin_page = (ROOT / "adapter" / "onvif-admin" / "static" / "index.html").read_text()
    admin_select = re.search(r'<select[^>]*outage[^>]*>(.*?)</select>', admin_page, re.S | re.I)
    copies = {
        "adapter/outage_buffer.py (0 = off, not stored)":
            py_constant(ROOT / "adapter" / "outage_buffer.py", "VALID_LIMITS") | {0},
        "adapter/onvif-admin/app.py": py_constant(ROOT / "adapter" / "onvif-admin" / "app.py", "VALID", within=ADMIN_OUTAGE_ROUTE),
        "adapter/onvif-admin/static/index.html":
            {int(v) for v in re.findall(r'<option value="(\d+)"', admin_select.group(1))} if admin_select else None,
        "client-app settings.ts": {int(v) for v in re.findall(r"sec: (\d+)", ts_block(WEB, "OUTAGE_LIMITS"))},
    }
    wrong = {name: sorted(v) if v else v for name, v in copies.items() if v != LIMITS}
    assert not wrong, f"set_camera_outage_buffer.py accepts {sorted(LIMITS)}; these differ: {wrong}"


def test_recording_modes_match_the_lambda():
    modes = py_constant(ROOT / "cloud" / "lambda" / "set_camera_mode.py", "VALID_MODES")
    web = set(re.findall(r'value: "(\w+)"', ts_block(WEB, "MODE_OPTIONS")))
    admin = py_constant(ROOT / "adapter" / "onvif-admin" / "app.py", "VALID", within="set_recording_mode")
    assert web == modes and admin == modes, (sorted(modes), sorted(web), sorted(admin))


def test_ir_modes_match_the_lambda():
    ir = py_constant(ROOT / "cloud" / "lambda" / "publish_cmd.py", "ALLOWED_IR_MODES")
    assert set(re.findall(r'value: "(\w+)"', ts_block(WEB, "IR_MODES"))) == ir


def test_live_layouts_match_the_user_settings_lambda():
    layouts = py_constant(ROOT / "cloud" / "lambda" / "user_settings.py", "LAYOUTS")
    grid = ROOT / "client-app" / "src" / "layouts" / "grid.ts"
    m = re.search(r"export const LAYOUT_IDS\b[^=]*=\s*\[(.*?)\];", grid.read_text(), re.S)
    assert m, "no LAYOUT_IDS in grid.ts"
    assert set(re.findall(r'"([^"]+)"', m.group(1))) == layouts


def test_storage_tiers_match_the_set_clip_tier_lambda():
    tiers = py_constant(ROOT / "cloud" / "lambda" / "set_clip_tier.py", "VALID_TIERS")
    fmt = ROOT / "client-app" / "src" / "clips" / "format.ts"
    assert set(re.findall(r'value: "(\w+)"', ts_block(fmt, "TIER_CHOICES"))) == tiers


def test_schedule_days_and_modes_match_between_the_lambda_and_the_page():
    lam = ROOT / "cloud" / "lambda" / "set_camera_settings.py"
    sched = ROOT / "client-app" / "src" / "cameras" / "schedule.ts"
    assert list(py_constant(lam, "DAYS")) == re.findall(r'"(\w+)"', ts_block(sched, "DAYS"))
    # The schedule's "mode:<recordingMode>" accepts exactly set-camera-mode's modes.
    assert py_constant(lam, "VALID_MODES") == py_constant(ROOT / "cloud" / "lambda" / "set_camera_mode.py", "VALID_MODES")
