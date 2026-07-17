from pathlib import Path

import pytest

from poppy.tool_context import ToolContext
from poppy.tools import build_tool_registry, tool_delegate, tool_read_file, tool_search


def test_tool_context_supports_file_tools_without_full_poppy(tmp_path):
    (tmp_path / "sample.txt").write_text("alpha\n", encoding="utf-8")
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: (tmp_path / raw_path).resolve(),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    result = tool_read_file(context, {"path": "sample.txt", "start": 1, "end": 1})

    assert "# sample.txt" in result
    assert "alpha" in result


def test_read_and_search_extract_pdf_without_binary_tool_trace(tmp_path):
    # Keep this test dependency-light: a small text PDF is produced with the
    # optional reportlab package when it is available in the desktop runtime.
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf_path = tmp_path / "notes.pdf"
    canvas = reportlab.Canvas(str(pdf_path))
    canvas.drawString(72, 720, "The run loop calls the model, executes tools, and records a trace.")
    canvas.save()

    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: (tmp_path / raw_path).resolve(),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    result = tool_read_file(context, {"path": "notes.pdf", "start": 1, "end": 20})
    matches = tool_search(context, {"path": "notes.pdf", "pattern": "run loop"})

    assert "run loop" in result
    assert "notes.pdf:" in matches
    assert "<tool>" not in result


def test_delegate_uses_context_spawn_without_runtime_import(tmp_path):
    calls = []
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=0,
        max_depth=1,
        spawn_delegate=lambda args: calls.append(args) or "delegate_result:\nDone",
    )

    result = tool_delegate(context, {"task": "inspect README.md", "max_steps": 2})

    assert result == "delegate_result:\nDone"
    assert calls == [{"task": "inspect README.md", "max_steps": 2}]


def test_build_tool_registry_binds_runners_to_tool_context(tmp_path):
    context = ToolContext(
        root=tmp_path,
        path_resolver=lambda raw_path: Path(tmp_path / raw_path),
        shell_env_provider=lambda: {"PWD": str(tmp_path)},
        depth=1,
        max_depth=1,
        spawn_delegate=lambda args: "unused",
    )

    tools = build_tool_registry(context)

    assert "read_file" in tools
    assert "delegate" not in tools
