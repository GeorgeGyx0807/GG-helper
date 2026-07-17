import subprocess
import sys
from pathlib import Path

import mini_poppy


def test_mini_poppy_module_and_public_exports():
    assert mini_poppy.Poppy is not None
    assert mini_poppy.FakeModelClient is not None
    assert not hasattr(mini_poppy, "MiniAgent")
    result = subprocess.run([sys.executable, "-m", "mini_poppy", "--help"], capture_output=True, text=True, check=True)
    assert "Teaching-sized Poppy agent harness" in result.stdout


def test_readme_main_mapping_points_to_existing_files():
    repo_root = Path(__file__).resolve().parents[3]
    main_files = [
        "poppy/cli.py",
        "poppy/runtime.py",
        "poppy/agent_loop.py",
        "poppy/context_manager.py",
        "poppy/providers/clients.py",
        "poppy/tool_executor.py",
        "poppy/tools.py",
        "poppy/task_state.py",
        "poppy/run_store.py",
        "poppy/workspace.py",
    ]
    for path in main_files:
        assert (repo_root / path).exists()
