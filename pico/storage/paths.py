"""Platform-aware paths for the Poppy desktop application."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def default(cls):
        override = os.environ.get("PICO_DESKTOP_DATA_DIR")
        if override:
            return cls(Path(override).expanduser().resolve())
        if sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "Poppy"
        elif sys.platform == "win32":
            root = Path(os.environ.get("APPDATA", Path.home())) / "Poppy"
        else:
            root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "pico"
        return cls(root.resolve())

    @property
    def database(self):
        return self.root / "pico.db"

    @property
    def sessions(self):
        return self.root / "sessions"

    @property
    def runs(self):
        return self.root / "runs"

    @property
    def memory(self):
        return self.root / "memory"

    @property
    def attachments(self):
        return self.root / "attachments"

    @property
    def logs(self):
        return self.root / "logs"

    def ensure(self):
        for path in (self.root, self.sessions, self.runs, self.memory, self.attachments, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        return self
