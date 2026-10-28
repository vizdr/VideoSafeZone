"""Repository rules that a search can check (PYTEST.md, step 5)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The code that runs on the Pi or in AWS, and the templates it is configured from. tests/ is left
# out: this file has to spell the very string it looks for.
CODE = [*(ROOT / "adapter").rglob("*.py"), *(ROOT / "adapter").rglob("*.sh"),
        *(ROOT / "cloud").rglob("*.py"), *(ROOT / "cloud").rglob("*.sh"),
        *(ROOT / "setup").rglob("*.sh"), *(p for p in (ROOT / "config").rglob("*") if p.is_file()),
        ROOT / "mediamtx" / "mediamtx.yml"]


def test_ff25_no_absolute_home_paths_in_code():
    # Absolute /home/<user>/MyProjects/VMS paths broke every script when the clone moved.
    offenders = []
    for path in CODE:
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if "/home/" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "derive the path instead (CLAUDE.md, 'Paths'):\n" + "\n".join(offenders)


def test_the_files_checked_exist():
    # An empty list would make the test above pass without checking anything.
    names = {p.name for p in CODE}
    assert {"camera_control.py", "producer-lib.sh", "deploy.sh", "lib.sh", "adapter.env.example",
            "mediamtx.yml"} <= names
