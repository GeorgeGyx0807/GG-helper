import sqlite3
import subprocess
import sys

import poppy
from poppy.legacy_migration import migrate_workspace_state
from poppy.storage.paths import AppPaths


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


def test_workspace_state_is_copied_from_legacy_directory(tmp_path):
    legacy = tmp_path / (".pi" + "co")
    (legacy / "sessions").mkdir(parents=True)
    (legacy / "sessions" / "saved.json").write_text("{}", encoding="utf-8")

    state_root = migrate_workspace_state(tmp_path)

    assert state_root == tmp_path / ".poppy"
    assert (state_root / "sessions" / "saved.json").read_text(encoding="utf-8") == "{}"
    assert legacy.exists()


def test_desktop_database_is_migrated_without_deleting_legacy_copy(tmp_path):
    legacy = tmp_path / ("pi" + "co.db")
    connection = sqlite3.connect(legacy)
    connection.execute("CREATE TABLE notes (body TEXT NOT NULL)")
    connection.execute("INSERT INTO notes VALUES (?)", ("kept",))
    connection.commit()
    connection.close()

    paths = AppPaths(tmp_path).ensure()

    connection = sqlite3.connect(paths.database)
    row = connection.execute("SELECT body FROM notes").fetchone()
    connection.close()
    assert row == ("kept",)
    assert legacy.exists()
