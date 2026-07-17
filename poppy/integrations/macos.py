"""Small, explicit macOS integrations.

All commands are passed as argument arrays (never through a shell).  The
agent still needs an approval before these tools run; this module only wraps
the OS APIs after the user has approved the operation.
"""

import datetime as _datetime
import re
import subprocess
import urllib.parse
import urllib.request


def _run(command, *, timeout=20):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"command exited with {result.returncode}")
    return result.stdout.strip()


def _require_macos():
    import sys
    if sys.platform != "darwin":
        raise RuntimeError("this integration is available only on macOS")


def clipboard_read():
    _require_macos()
    return _run(["pbpaste"], timeout=5) or "(clipboard is empty)"


def clipboard_write(text):
    _require_macos()
    process = subprocess.run(["pbcopy"], input=str(text), capture_output=True, text=True, timeout=5)
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or "pbcopy failed")
    return f"clipboard updated ({len(str(text))} chars)"


def browser_open(url):
    parsed = _validate_url(url)
    _require_macos()
    _run(["open", parsed.geturl()], timeout=10)
    return f"opened {parsed.geturl()}"


def web_read(url, max_chars=12000):
    parsed = _validate_url(url)
    request = urllib.request.Request(parsed.geturl(), headers={"User-Agent": "Poppy/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(512_000)
        charset = response.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="replace")
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(100, min(int(max_chars), 30_000))] or "(page has no readable text)"


def reminder_create(title, list_name="Reminders", notes="", due_at=""):
    _require_macos()
    title = _literal(title)
    list_name = _literal(list_name or "Reminders")
    notes = _literal(notes)
    due_command = ""
    if due_at:
        due = _parse_calendar_date(due_at)
        due_command = f'  set due date of newReminder to date "{due.strftime("%m/%d/%Y %H:%M:%S")}"\n'
    script = (
        f'tell application "Reminders"\n'
        f'  set targetList to list "{list_name}"\n'
        f'  set newReminder to make new reminder at targetList with properties {{name:"{title}"}}\n'
        f'  set body of newReminder to "{notes}"\n'
        f'{due_command}'
        f'end tell'
    )
    _run(["osascript", "-e", script], timeout=15)
    return f"created reminder: {title}"


def reminder_list(list_name=""):
    _require_macos()
    if list_name:
        script = f'tell application "Reminders" to get name of every reminder in list "{_literal(list_name)}"'
    else:
        script = 'tell application "Reminders" to get name of every reminder'
    return _run(["osascript", "-e", script], timeout=15) or "(no reminders)"


def calendar_create(title, start_at, end_at="", calendar_name="Calendar", notes=""):
    _require_macos()
    start = _parse_calendar_date(start_at)
    end = _parse_calendar_date(end_at) if end_at else start + _datetime.timedelta(minutes=30)
    script = (
        f'tell application "Calendar"\n'
        f'  tell calendar "{_literal(calendar_name or "Calendar")}"\n'
        f'    make new event with properties {{summary:"{_literal(title)}", start date:date "{start.strftime("%m/%d/%Y %H:%M:%S")}", end date:date "{end.strftime("%m/%d/%Y %H:%M:%S")}", description:"{_literal(notes)}"}}\n'
        f'  end tell\n'
        f'end tell'
    )
    _run(["osascript", "-e", script], timeout=15)
    return f"created calendar event: {title}"


def calendar_list(calendar_name=""):
    _require_macos()
    if calendar_name:
        script = f'tell application "Calendar" to get summary of every event of calendar "{_literal(calendar_name)}"'
    else:
        script = 'tell application "Calendar" to get summary of every event of every calendar'
    return _run(["osascript", "-e", script], timeout=15) or "(no calendar events)"


def _validate_url(url):
    parsed = urllib.parse.urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http or https URL")
    if any(ch in parsed.netloc for ch in "\r\n"):
        raise ValueError("invalid URL")
    return parsed


def _literal(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _parse_calendar_date(value):
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = _datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("calendar date must be ISO-8601, e.g. 2026-07-14T10:00:00") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed
