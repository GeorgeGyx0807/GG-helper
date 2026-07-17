"""One-time migration helpers for workspaces created before the Poppy rename."""

from pathlib import Path
import shutil


def migrate_workspace_state(repo_root):
    """Copy the former hidden state directory once, preserving rollback data."""
    root = Path(repo_root).expanduser().resolve()
    target = root / ".poppy"
    legacy = root / (".pi" + "co")
    if not target.exists() and legacy.is_dir():
        shutil.copytree(legacy, target)
    return target
