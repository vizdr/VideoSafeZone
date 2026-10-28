"""Copies of one function across Lambdas stay the same (PYTEST.md, step 5).

Each Lambda deploys as a single file (cloud/deploy.sh zips one .py), so shared helpers are copied
rather than imported: record_clip.py and clip_to_s3.py share the MP4 and codec-window helpers, and
five Lambdas share unregistered_response. A fix made to one copy and not the other is how they
would drift. This test compares the code of every function defined in more than one Lambda --
docstrings and comments aside, which may differ -- so a new copy is checked without being listed.
"""
import ast
from pathlib import Path

import pytest

LAMBDA_DIR = Path(__file__).resolve().parent.parent.parent / "cloud" / "lambda"
NOT_SHARED = {"lambda_handler"}      # every Lambda has its own


def _code(fn: ast.FunctionDef) -> str:
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]                                  # the docstring
    return ast.dump(ast.Module(body=body, type_ignores=[])) + ast.dump(fn.args)


def _functions():
    found = {}
    for path in sorted(LAMBDA_DIR.glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name not in NOT_SHARED:
                found.setdefault(node.name, {})[path.name] = _code(node)
    return {name: files for name, files in found.items() if len(files) > 1}


SHARED = _functions()


def test_the_known_copies_are_found():
    assert {"utc", "codec_windows", "mp4_video_codec", "_child", "mp4_duration_sec",
            "no_fragments", "unregistered_response"} <= set(SHARED)


@pytest.mark.parametrize("name", sorted(SHARED))
def test_copies_are_identical(name):
    files = SHARED[name]
    variants = {}
    for file, code in files.items():
        variants.setdefault(code, []).append(file)
    assert len(variants) == 1, f"{name} differs between: {list(variants.values())}"
