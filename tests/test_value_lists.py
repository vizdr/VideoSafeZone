"""The value lists the pages offer match what the Lambdas accept (WEB-PANEL-PLAN.md Phase 1).

A choice is duplicated wherever it is offered or checked: the outage-buffer durations live in the
Lambda, the adapter, the admin GUI's route and page, the old cloud page and the web panel. A copy
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
    old_page = (ROOT / "client" / "index.html").read_text()
    admin_page = (ROOT / "adapter" / "onvif-admin" / "static" / "index.html").read_text()
    admin_select = re.search(r'<select[^>]*outage[^>]*>(.*?)</select>', admin_page, re.S | re.I)
    copies = {
        "adapter/outage_buffer.py (0 = off, not stored)":
            py_constant(ROOT / "adapter" / "outage_buffer.py", "VALID_LIMITS") | {0},
        "adapter/onvif-admin/app.py": py_constant(ROOT / "adapter" / "onvif-admin" / "app.py", "VALID", within=ADMIN_OUTAGE_ROUTE),
        "adapter/onvif-admin/static/index.html":
            {int(v) for v in re.findall(r'<option value="(\d+)"', admin_select.group(1))} if admin_select else None,
        "client/index.html": {int(v) for v in re.findall(r"\{ sec: (\d+),", old_page)},
        "client-app settings.ts": {int(v) for v in re.findall(r"sec: (\d+)", ts_block(WEB, "OUTAGE_LIMITS"))},
    }
    wrong = {name: sorted(v) if v else v for name, v in copies.items() if v != LIMITS}
    assert not wrong, f"set_camera_outage_buffer.py accepts {sorted(LIMITS)}; these differ: {wrong}"


def test_recording_modes_match_the_lambda():
    modes = py_constant(ROOT / "cloud" / "lambda" / "set_camera_mode.py", "VALID_MODES")
    web = set(re.findall(r'value: "(\w+)"', ts_block(WEB, "MODE_OPTIONS")))
    old = set(re.findall(r'<option value="(\w+)"',
                         re.search(r'id="recMode-.*?</select>', (ROOT / "client" / "index.html").read_text(), re.S).group(0)))
    assert web == modes and old == modes, (sorted(modes), sorted(web), sorted(old))


def test_ir_modes_match_the_lambda():
    ir = py_constant(ROOT / "cloud" / "lambda" / "publish_cmd.py", "ALLOWED_IR_MODES")
    assert set(re.findall(r'value: "(\w+)"', ts_block(WEB, "IR_MODES"))) == ir
