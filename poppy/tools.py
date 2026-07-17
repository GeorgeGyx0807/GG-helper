"""工具定义与执行辅助逻辑。

可以把这个文件看成 agent 的能力白名单：模型能申请哪些动作、这些动作
如何做参数校验，以及最终如何执行，都是在这里定义的。
"""

import os
import re
import signal
import shutil
import subprocess
import textwrap
import time
from functools import partial

from .features.document_extractors import DOCUMENT_EXTENSIONS, DocumentExtractionError, extract_document
from .features.document_index import TEXT_EXTENSIONS
from .integrations import macos
from .workspace import IGNORED_PATH_NAMES

BASE_TOOL_SPECS = {
    "list_files": {
        "schema": {"path": "str='.'"},
        "risky": False,
        "description": "List files in the workspace.",
    },
    "read_file": {
        "schema": {"path": "str", "start": "int=1", "end": "int=200"},
        "risky": False,
        "description": "Read a text file by line range; PDF, Word, and Excel files are extracted locally as text.",
    },
    "search": {
        "schema": {"pattern": "str", "path": "str='.'"},
        "risky": False,
        "description": "Search the workspace with rg or a simple fallback.",
    },
    "run_shell": {
        "schema": {"command": "str", "timeout": "int=20"},
        "risky": True,
        "description": "Run a shell command in the repo root.",
    },
    "write_file": {
        "schema": {"path": "str", "content": "str"},
        "risky": True,
        "description": "Write a text file.",
    },
    "patch_file": {
        "schema": {"path": "str", "old_text": "str", "new_text": "str"},
        "risky": True,
        "description": "Replace one exact text block in a file.",
    },
    "library_search": {
        "schema": {"query": "str", "limit": "int=10"},
        "risky": False,
        "description": "Search indexed text in user-authorized personal folders.",
    },
    "clipboard_read": {
        "schema": {},
        "risky": True,
        "description": "Read the current macOS clipboard after user approval.",
    },
    "clipboard_write": {
        "schema": {"text": "str"},
        "risky": True,
        "description": "Replace the current macOS clipboard after user approval.",
    },
    "browser_open": {
        "schema": {"url": "str"},
        "risky": True,
        "description": "Open an HTTP(S) URL in the default browser after approval.",
    },
    "web_read": {
        "schema": {"url": "str", "max_chars": "int=12000"},
        "risky": True,
        "description": "Fetch readable text from an HTTP(S) page after approval.",
    },
    "reminder_create": {
        "schema": {"title": "str", "list_name": "str='Reminders'", "notes": "str=''", "due_at": "str=''"},
        "risky": True,
        "description": "Create a macOS reminder after approval.",
    },
    "reminder_list": {
        "schema": {"list_name": "str=''"},
        "risky": True,
        "description": "Read macOS reminders after approval.",
    },
    "calendar_create": {
        "schema": {"title": "str", "start_at": "str", "end_at": "str=''", "calendar_name": "str='Calendar'", "notes": "str=''"},
        "risky": True,
        "description": "Create a macOS calendar event after approval.",
    },
    "calendar_list": {
        "schema": {"calendar_name": "str=''"},
        "risky": True,
        "description": "Read macOS calendar events after approval.",
    },
}

DELEGATE_TOOL_SPEC = {
    "schema": {"task": "str", "max_steps": "int=3"},
    "risky": False,
    "description": "Ask a bounded read-only child agent to investigate.",
}


def legal_tool_names():
    return set(BASE_TOOL_SPECS) | {"delegate"}

TOOL_EXAMPLES = {
    "list_files": '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
    "read_file": '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
    "search": '<tool>{"name":"search","args":{"pattern":"binary_search","path":"."}}</tool>',
    "run_shell": '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
    "write_file": '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
    "patch_file": '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
    "library_search": '<tool>{"name":"library_search","args":{"query":"会议纪要","limit":10}}</tool>',
    "delegate": '<tool>{"name":"delegate","args":{"task":"inspect README.md","max_steps":3}}</tool>',
}


def build_tool_registry(context):
    # 工具不是动态发现的，而是显式注册的。
    # 这样模型看到的是一个有边界、可审计的动作集合。
    tools = {
        name: {**spec, "run": partial(_TOOL_RUNNERS[name], context)}
        for name, spec in BASE_TOOL_SPECS.items()
    }
    # 子 agent 是刻意做成受限能力的：一旦深度耗尽，
    # 就连 delegate 这个工具都不再暴露给模型。
    if context.depth < context.max_depth:
        tools["delegate"] = {**DELEGATE_TOOL_SPEC, "run": partial(tool_delegate, context)}
    return tools


def tool_example(name):
    return TOOL_EXAMPLES.get(name, "")


def validate_tool(context, name, args):
    args = args or {}

    if name == "list_files":
        path = context.path(args.get("path", "."))
        if not path.is_dir():
            raise ValueError("path is not a directory")
        return

    if name == "read_file":
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        start = int(args.get("start", 1))
        end = int(args.get("end", 200))
        if start < 1 or end < start:
            raise ValueError("invalid line range")
        return

    if name == "search":
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern must not be empty")
        context.path(args.get("path", "."))
        return

    if name == "run_shell":
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        return

    if name == "write_file":
        path = context.path(args["path"])
        if path.exists() and path.is_dir():
            raise ValueError("path is a directory")
        if "content" not in args:
            raise ValueError("missing content")
        return

    if name == "patch_file":
        # patch_file 故意做得很严格：old_text 必须精确命中且只能出现一次，
        # 这样修改行为才是确定的，失败原因也更容易解释。
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(args.get("old_text", ""))
        if not old_text:
            raise ValueError("old_text must not be empty")
        if "new_text" not in args:
            raise ValueError("missing new_text")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
        return

    if name == "library_search":
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("query must not be empty")
        limit = int(args.get("limit", 10))
        if limit < 1 or limit > 50:
            raise ValueError("limit must be in [1, 50]")
        if context.library_searcher is None:
            raise ValueError("personal library is not available")
        return

    if name in {"clipboard_read", "reminder_list", "calendar_list"}:
        return

    if name in {"clipboard_write", "browser_open", "web_read", "reminder_create", "calendar_create"}:
        if name == "clipboard_write" and "text" not in args:
            raise ValueError("missing text")
        if name in {"browser_open", "web_read"}:
            macos._validate_url(args.get("url", ""))
        if name in {"reminder_create", "calendar_create"} and not str(args.get("title", "")).strip():
            raise ValueError("title must not be empty")
        if name == "reminder_create" and args.get("due_at"):
            macos._parse_calendar_date(args.get("due_at"))
        if name == "calendar_create":
            macos._parse_calendar_date(args.get("start_at", ""))
            if args.get("end_at"):
                macos._parse_calendar_date(args.get("end_at"))
        return

    if name == "delegate":
        task = str(args.get("task", "")).strip()
        if not task:
            raise ValueError("task must not be empty")
        if context.depth >= context.max_depth:
            raise ValueError("delegate depth exceeded")
        return


def tool_list_files(context, args):
    path = context.path(args.get("path", "."))
    if not path.is_dir():
        raise ValueError("path is not a directory")
    entries = [
        item for item in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        if item.name not in IGNORED_PATH_NAMES
    ]
    lines = []
    for entry in entries[:200]:
        kind = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"{kind} {entry.relative_to(context.root)}")
    return "\n".join(lines) or "(empty)"


def tool_read_file(context, args):
    path = context.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    start = int(args.get("start", 1))
    end = int(args.get("end", 200))
    if start < 1 or end < start:
        raise ValueError("invalid line range")
    if path.suffix.lower() in DOCUMENT_EXTENSIONS:
        try:
            extracted = extract_document(path, TEXT_EXTENSIONS)
        except DocumentExtractionError as exc:
            raise ValueError(str(exc)) from exc
        lines = _document_lines(path, extracted)
    else:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(f"{number:>4}: {line}" for number, line in enumerate(lines[start - 1:end], start=start))
    return f"# {path.relative_to(context.root)}\n{body}"


def _document_lines(path, extracted):
    """Render extracted document chunks as line-addressable tool output.

    `read_file` keeps its existing line-range contract so a model can request
    another range after the first 4,000-character tool result.  Locator lines
    make the answer traceable back to a PDF page, Word conversion range, or
    spreadsheet sheet/row range.
    """
    lines = []
    for chunk in extracted.chunks:
        location = chunk.get("location") or {}
        kind = location.get("kind")
        if kind == "pdf_page":
            label = f"第 {location.get('page', '?')} 页"
        elif kind == "spreadsheet":
            label = (
                f"工作表 {location.get('sheet', '?')} · "
                f"第 {location.get('row_start', '?')}-{location.get('row_end', '?')} 行"
            )
        elif kind == "docx":
            label = f"Word 转换文本行 {chunk.get('line_start', '?')}-{chunk.get('line_end', '?')}"
        elif kind == "pptx":
            label = f"PowerPoint 转换文本行 {chunk.get('line_start', '?')}-{chunk.get('line_end', '?')}"
        else:
            label = f"文本行 {chunk.get('line_start', '?')}-{chunk.get('line_end', '?')}"
        lines.append(f"[{path.name} · {label}]")
        lines.extend(str(chunk.get("content") or "").splitlines())
    return lines


def tool_search(context, args):
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    path = context.path(args.get("path", "."))

    if path.is_file() and path.suffix.lower() in DOCUMENT_EXTENSIONS:
        return _search_document(context, path, pattern)

    if shutil.which("rg"):
        # 优先用 rg，因为搜索会非常频繁，搜索延迟会直接影响 agent 控制循环。
        result = subprocess.run(
            ["rg", "-n", "--smart-case", "--max-count", "200", pattern, str(path)],
            cwd=context.root,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or result.stderr.strip() or "(no matches)"

    matches = []
    files = [path] if path.is_file() else [
        item for item in path.rglob("*")
        if item.is_file() and not any(part in IGNORED_PATH_NAMES for part in item.relative_to(context.root).parts)
    ]
    for file_path in files:
        for number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if pattern.lower() in line.lower():
                matches.append(f"{file_path.relative_to(context.root)}:{number}:{line}")
                if len(matches) >= 200:
                    return "\n".join(matches)
    return "\n".join(matches) or "(no matches)"


def _search_document(context, path, pattern):
    try:
        extracted = extract_document(path, TEXT_EXTENSIONS)
    except DocumentExtractionError as exc:
        raise ValueError(str(exc)) from exc
    try:
        matcher = re.compile(pattern, re.IGNORECASE)

        def matches(line):
            return matcher.search(line) is not None
    except re.error:
        lowered = pattern.casefold()

        def matches(line):
            return lowered in line.casefold()
    lines = _document_lines(path, extracted)
    output = []
    for number, line in enumerate(lines, start=1):
        if matches(line):
            output.append(f"{path.relative_to(context.root)}:{number}:{line}")
            if len(output) >= 200:
                break
    return "\n".join(output) or "(no matches)"


def tool_run_shell(context, args):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError("command must not be empty")
    timeout = int(args.get("timeout", 20))
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be in [1, 120]")
    context.raise_if_cancelled()
    process = subprocess.Popen(
        command,
        cwd=context.root,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        # 这里传入的是过滤后的环境变量，而不是直接继承整个父 shell 环境，
        # 目的是减少敏感信息被意外带进命令执行环境的风险。
        env=context.shell_env(),
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            context.raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        _terminate_process_group(process)
        raise
    return textwrap.dedent(
        f"""\
        exit_code: {process.returncode}
        stdout:
        {stdout.strip() or "(empty)"}
        stderr:
        {stderr.strip() or "(empty)"}
        """
    ).strip()


def _terminate_process_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)


def tool_write_file(context, args):
    path = context.path(args["path"])
    content = str(args["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {path.relative_to(context.root)} ({len(content)} chars)"


def tool_patch_file(context, args):
    path = context.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    old_text = str(args.get("old_text", ""))
    if not old_text:
        raise ValueError("old_text must not be empty")
    if "new_text" not in args:
        raise ValueError("missing new_text")
    text = path.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count != 1:
        raise ValueError(f"old_text must occur exactly once, found {count}")
    path.write_text(text.replace(old_text, str(args["new_text"]), 1), encoding="utf-8")
    return f"patched {path.relative_to(context.root)}"


def tool_delegate(context, args):
    if context.depth >= context.max_depth:
        raise ValueError("delegate depth exceeded")
    task = str(args.get("task", "")).strip()
    if not task:
        raise ValueError("task must not be empty")
    return context.spawn_delegate(args)


def tool_library_search(context, args):
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 10))
    rows = context.library_searcher(query, limit)
    if not rows:
        return "(no indexed matches)"
    return "\n\n".join(
        f"{_library_location(row)}\n{row['content']}"
        for row in rows
    )


def _library_location(row):
    location = row.get("location") or {}
    kind = location.get("kind")
    if kind == "pdf_page":
        page = location.get("page", "?")
        return f"{row['path']} · 第 {page} 页（转换文本行 {row['line_start']}-{row['line_end']}）"
    if kind == "spreadsheet":
        sheet = location.get("sheet", "?")
        start = location.get("row_start", row["line_start"])
        end = location.get("row_end", row["line_end"])
        return f"{row['path']} · 工作表 {sheet} · 第 {start}-{end} 行"
    if kind == "docx":
        return f"{row['path']} · Word 转换文本行 {row['line_start']}-{row['line_end']}"
    if kind == "pptx":
        return f"{row['path']} · PowerPoint 转换文本行 {row['line_start']}-{row['line_end']}"
    return f"{row['path']}:{row['line_start']}-{row['line_end']}"


def tool_clipboard_read(_context, _args):
    return macos.clipboard_read()


def tool_clipboard_write(_context, args):
    return macos.clipboard_write(args.get("text", ""))


def tool_browser_open(_context, args):
    return macos.browser_open(args.get("url", ""))


def tool_web_read(_context, args):
    return macos.web_read(args.get("url", ""), args.get("max_chars", 12000))


def tool_reminder_create(_context, args):
    return macos.reminder_create(args.get("title", ""), args.get("list_name", "Reminders"), args.get("notes", ""), args.get("due_at", ""))


def tool_reminder_list(_context, args):
    return macos.reminder_list(args.get("list_name", ""))


def tool_calendar_create(_context, args):
    return macos.calendar_create(args.get("title", ""), args.get("start_at", ""), args.get("end_at", ""), args.get("calendar_name", "Calendar"), args.get("notes", ""))


def tool_calendar_list(_context, args):
    return macos.calendar_list(args.get("calendar_name", ""))


_TOOL_RUNNERS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "search": tool_search,
    "run_shell": tool_run_shell,
    "write_file": tool_write_file,
    "patch_file": tool_patch_file,
    "library_search": tool_library_search,
    "clipboard_read": tool_clipboard_read,
    "clipboard_write": tool_clipboard_write,
    "browser_open": tool_browser_open,
    "web_read": tool_web_read,
    "reminder_create": tool_reminder_create,
    "reminder_list": tool_reminder_list,
    "calendar_create": tool_calendar_create,
    "calendar_list": tool_calendar_list,
}
