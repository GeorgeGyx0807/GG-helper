"""Platform-aware paths for the Poppy desktop application."""

import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def default(cls):
        override = os.environ.get("POPPY_DESKTOP_DATA_DIR")
        if override:
            return cls(Path(override).expanduser().resolve())
        if sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "Poppy"
        elif sys.platform == "win32":
            root = Path(os.environ.get("APPDATA", Path.home())) / "Poppy"
        else:
            root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "poppy"
        return cls(root.resolve())

    @property
    def database(self):
        return self.root / "poppy.db"

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
    def feishu_attachments(self):
        return self.root / "feishu"

    @property
    def logs(self):
        return self.root / "logs"

    @property
    def documents(self):
        """Normalized copies/text extracted from user-authorized sources.

        The index never uses this directory as an authority boundary; grants
        remain the source of truth.  Keeping derived data in the app data
        directory makes it easy to rebuild or delete without touching the
        user's original files.
        """
        return self.root / "documents"

    @property
    def extracted(self):
        return self.root / "extracted"

    @property
    def vectors(self):
        return self.root / "vectors"

    @property
    def models(self):
        return self.root / "models"

    @property
    def backups(self):
        return self.root / "backups"

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_database()
        for path in (
            self.root,
            self.sessions,
            self.runs,
            self.memory,
            self.attachments,
            self.feishu_attachments,
            self.logs,
            self.documents,
            self.extracted,
            self.vectors,
            self.models,
            self.backups,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def _migrate_legacy_database(self):
        if self.database.exists():
            return
        legacy = self.root / ("pi" + "co.db")
        if not legacy.is_file():
            return
        staging = self.database.with_suffix(".db.migrating")
        staging.unlink(missing_ok=True)
        source = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
        target = sqlite3.connect(staging)
        try:
            source.backup(target)
            target.close()
            target = None
            staging.replace(self.database)
        finally:
            if target is not None:
                target.close()
            source.close()
            staging.unlink(missing_ok=True)
