"""Import the files Python can't import by name (PYTEST.md, step 4).

cloud/lambda/*.py are single-file Lambdas, not a package, and adapter/bin/*.py have hyphens in
their names. Both are loaded from their path under a unique module name, once per test run:

    from loaders import load_lambda, load_script
    record_clip = load_lambda("record_clip")
    gap_fill = load_script("gap-fill")

A Lambda reads AWS_REGION and BUCKET and builds its boto3 resources when imported. conftest.py has
set fake values and fake credentials by then, and building a resource sends nothing, so importing
one is offline. Tests replace the module's tables and clients (monkeypatch.setattr) before calling
a handler.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path, module_name: str):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def load_lambda(name: str):
    """cloud/lambda/<name>.py, e.g. load_lambda("record_clip")."""
    return _load(ROOT / "cloud" / "lambda" / f"{name}.py", f"lambda_{name}")


def load_script(name: str):
    """adapter/bin/<name>.py, e.g. load_script("gap-fill")."""
    return _load(ROOT / "adapter" / "bin" / f"{name}.py", "script_" + name.replace("-", "_"))
