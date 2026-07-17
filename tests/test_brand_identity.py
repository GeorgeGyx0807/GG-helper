import re
import subprocess
import sys
from pathlib import Path

import poppy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMER_BRAND = "pi" + "co"
FORMER_BRAND_PATTERN = re.compile(
    rf"(?<![a-z0-9_]){re.escape(FORMER_BRAND)}(?![a-z0-9_])",
    re.IGNORECASE,
)


def test_public_package_and_cli_use_poppy_brand():
    assert poppy.Poppy.__name__ == "Poppy"
    result = subprocess.run(
        [sys.executable, "-m", "poppy", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "poppy" in result.stdout.lower()
    assert "POPPY_" in result.stdout


def test_tracked_paths_and_text_use_only_poppy_brand():
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    tracked_paths = [
        Path(raw.decode("utf-8"))
        for raw in result.stdout.split(b"\0")
        if raw
    ]

    path_violations = [
        path.as_posix()
        for path in tracked_paths
        if FORMER_BRAND_PATTERN.search(path.as_posix())
    ]
    content_violations = []
    for relative_path in tracked_paths:
        path = PROJECT_ROOT / relative_path
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if FORMER_BRAND_PATTERN.search(content):
            content_violations.append(relative_path.as_posix())

    assert not path_violations, path_violations
    assert not content_violations, content_violations
