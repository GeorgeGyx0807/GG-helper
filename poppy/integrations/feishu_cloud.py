"""Read-only Feishu cloud resource ingestion for the desktop bridge.

The module intentionally converts remote resources into session-scoped
Markdown snapshots.  The existing local document index remains the only
retrieval path exposed to the model, so cloud content inherits the same
attachment isolation, citation, and read-only guarantees as uploaded files.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen


FEISHU_API_ROOT = "https://open.feishu.cn/open-apis"
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.I)
CALENDAR_COMMAND_PATTERN = re.compile(
    r"^\s*(?:请|帮我|请帮我)?\s*(?:/calendar|/日历|读取(?:一下)?飞书日历|"
    r"查看(?:一下)?飞书日历|查询(?:一下)?飞书日历)(?:[\s，,；;。.]|$)",
    re.I,
)
DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
MAX_RESOURCES_PER_MESSAGE = 4
MAX_SNAPSHOT_CHARS = 1_800_000
MAX_BITABLE_TABLES = 8
MAX_BITABLE_RECORDS = 500
MAX_CALENDARS = 8
MAX_CALENDAR_EVENTS = 200

READ_SCOPE_IDS = (
    "docx:document:readonly",
    "wiki:node:read",
    "bitable:app:readonly",
    "calendar:calendar:readonly",
)


@dataclass(frozen=True)
class CloudReference:
    kind: str
    token: str = ""
    source_url: str = ""
    table_id: str = ""
    view_id: str = ""
    calendar_id: str = ""
    event_id: str = ""
    start_date: str = ""
    end_date: str = ""

    @property
    def cache_key(self):
        return "|".join(
            (
                self.kind,
                self.token,
                self.table_id,
                self.view_id,
                self.calendar_id,
                self.event_id,
                self.start_date,
                self.end_date,
            )
        )


@dataclass(frozen=True)
class CloudSnapshot:
    kind: str
    title: str
    source_url: str
    path: str
    truncated: bool = False


class FeishuCloudError(RuntimeError):
    """A safe, user-facing cloud read failure."""

    def __init__(self, message, *, code=0, resource_kind=""):
        super().__init__(str(message))
        self.code = int(code or 0)
        self.resource_kind = str(resource_kind or "")


class FeishuOpenAPIError(RuntimeError):
    def __init__(self, message, *, code=0, status=0):
        super().__init__(str(message))
        self.code = int(code or 0)
        self.status = int(status or 0)


class FeishuOpenAPI:
    """Small tenant-token client with bounded requests and no secret logging."""

    def __init__(self, app_id, app_secret, *, timeout=15, opener=urlopen):
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.timeout = max(3, min(int(timeout), 30))
        self.opener = opener
        self._lock = threading.RLock()
        self._access_token = ""
        self._expires_at = 0.0

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def _tenant_access_token(self):
        with self._lock:
            if self._access_token and time.monotonic() < self._expires_at:
                return self._access_token
            body = self._open_json(
                Request(
                    f"{FEISHU_API_ROOT}/auth/v3/tenant_access_token/internal",
                    data=json.dumps(
                        {"app_id": self.app_id, "app_secret": self.app_secret},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
            )
            if int(body.get("code") or 0) != 0:
                raise FeishuOpenAPIError(
                    body.get("msg") or "无法获取飞书 tenant_access_token",
                    code=body.get("code") or 0,
                )
            token = str(body.get("tenant_access_token") or "").strip()
            if not token:
                raise FeishuOpenAPIError("飞书未返回 tenant_access_token")
            expires = max(60, int(body.get("expire") or 7200))
            self._access_token = token
            self._expires_at = time.monotonic() + max(30, expires - 120)
            return token

    def _request(self, method, path, params=None):
        query = urlencode(
            [(str(key), str(value)) for key, value in (params or {}).items() if value not in (None, "")]
        )
        url = f"{FEISHU_API_ROOT}/{str(path).lstrip('/')}"
        if query:
            url += "?" + query
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self._tenant_access_token()}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method=method,
        )
        body = self._open_json(request)
        code = int(body.get("code") or 0)
        if code != 0:
            raise FeishuOpenAPIError(body.get("msg") or "飞书 OpenAPI 调用失败", code=code)
        return body.get("data") or {}

    def _open_json(self, request):
        try:
            response = self.opener(request, timeout=self.timeout)
            raw = response.read()
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace"))
            except Exception:
                body = {}
            raise FeishuOpenAPIError(
                body.get("msg") or f"飞书 OpenAPI 返回 HTTP {exc.code}",
                code=body.get("code") or 0,
                status=exc.code,
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise FeishuOpenAPIError(f"连接飞书 OpenAPI 失败：{exc}") from None
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FeishuOpenAPIError("飞书 OpenAPI 返回了无法解析的数据") from None
        if not isinstance(body, dict):
            raise FeishuOpenAPIError("飞书 OpenAPI 返回格式异常")
        return body


class FeishuCloudReader:
    def __init__(self, app_id, app_secret, cache_root, *, api=None, now=None):
        self.app_id = str(app_id or "").strip()
        self.api = api or FeishuOpenAPI(app_id, app_secret)
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def parse_references(cls, text):
        raw_text = str(text or "")
        references = []
        for raw_url in URL_PATTERN.findall(raw_text):
            url = raw_url.rstrip(".,;:!?，。；：！？、)]}）】》")
            parsed = urlparse(url)
            host = parsed.netloc.lower().split(":", 1)[0]
            if not (
                host == "feishu.cn"
                or host.endswith(".feishu.cn")
                or host == "larksuite.com"
                or host.endswith(".larksuite.com")
            ):
                continue
            segments = [segment for segment in parsed.path.split("/") if segment]
            if len(segments) < 2:
                continue
            resource_type, token = segments[0].lower(), segments[1]
            query = parse_qs(parsed.query)
            if resource_type == "docx":
                references.append(CloudReference("docx", token=token, source_url=url))
            elif resource_type == "wiki":
                references.append(
                    CloudReference(
                        "wiki",
                        token=token,
                        source_url=url,
                        table_id=cls._first(query, "table", "table_id"),
                        view_id=cls._first(query, "view", "view_id"),
                    )
                )
            elif resource_type in {"base", "bitable"}:
                references.append(
                    CloudReference(
                        "bitable",
                        token=token,
                        source_url=url,
                        table_id=cls._first(query, "table", "table_id"),
                        view_id=cls._first(query, "view", "view_id"),
                    )
                )
            elif resource_type == "calendar":
                references.append(
                    CloudReference(
                        "calendar",
                        source_url=url,
                        calendar_id=cls._first(query, "calendar_id", "calendarId"),
                        event_id=cls._first(query, "event_id", "eventId"),
                    )
                )
        if not any(item.kind == "calendar" for item in references) and CALENDAR_COMMAND_PATTERN.search(raw_text):
            dates = DATE_PATTERN.findall(raw_text)
            references.append(
                CloudReference(
                    "calendar",
                    start_date=dates[0] if dates else "",
                    end_date=dates[1] if len(dates) > 1 else "",
                )
            )
        unique = []
        seen = set()
        for item in references:
            if item.cache_key in seen:
                continue
            seen.add(item.cache_key)
            unique.append(item)
            if len(unique) >= MAX_RESOURCES_PER_MESSAGE:
                break
        return unique

    @staticmethod
    def strip_resource_urls(text):
        return URL_PATTERN.sub("", str(text or "")).strip()

    def read_message(self, text, session_id):
        snapshots = []
        for reference in self.parse_references(text):
            try:
                title, content = self._read_reference(reference)
            except FeishuOpenAPIError as exc:
                raise self._friendly_error(reference, exc) from None
            snapshots.append(self._persist(reference, session_id, title, content))
        return snapshots

    def _read_reference(self, reference):
        if reference.kind == "docx":
            return self._read_docx(reference.token, reference.source_url)
        if reference.kind == "wiki":
            return self._read_wiki(reference)
        if reference.kind == "bitable":
            return self._read_bitable(reference.token, reference)
        if reference.kind == "calendar":
            return self._read_calendar(reference)
        raise FeishuCloudError("暂不支持这个飞书云资源类型。", resource_kind=reference.kind)

    def _read_docx(self, document_id, source_url="", title_hint=""):
        document_id = quote(str(document_id), safe="")
        metadata = self.api.get(f"docx/v1/documents/{document_id}")
        raw = self.api.get(f"docx/v1/documents/{document_id}/raw_content")
        document = metadata.get("document") or {}
        title = str(title_hint or document.get("title") or "飞书云文档").strip()
        content = str(raw.get("content") or "").strip()
        if not content:
            raise FeishuCloudError("飞书云文档没有可读取的纯文本内容。", resource_kind="docx")
        return title, self._markdown_header("飞书云文档", title, source_url) + content

    def _read_wiki(self, reference):
        data = self.api.get("wiki/v2/spaces/get_node", {"token": reference.token})
        node = data.get("node") or {}
        obj_type = str(node.get("obj_type") or "")
        obj_token = str(node.get("obj_token") or "")
        title = str(node.get("title") or "飞书知识库").strip()
        if not obj_token:
            raise FeishuCloudError("知识库节点没有关联可读取的云资源。", resource_kind="wiki")
        if obj_type == "docx":
            return self._read_docx(obj_token, reference.source_url, title_hint=title)
        if obj_type == "bitable":
            forwarded = CloudReference(
                "bitable",
                token=obj_token,
                source_url=reference.source_url,
                table_id=reference.table_id,
                view_id=reference.view_id,
            )
            return self._read_bitable(obj_token, forwarded, title_hint=title)
        raise FeishuCloudError(
            f"这个知识库节点挂载的是 {obj_type or '未知类型'}；当前直接读取支持新版文档和多维表格。",
            resource_kind="wiki",
        )

    def _read_bitable(self, app_token, reference, title_hint=""):
        token = quote(str(app_token), safe="")
        metadata = self.api.get(f"bitable/v1/apps/{token}")
        app = metadata.get("app") or {}
        title = str(title_hint or app.get("name") or "飞书多维表格").strip()
        if reference.table_id:
            tables = [{"table_id": reference.table_id, "name": reference.table_id}]
        else:
            data = self.api.get(f"bitable/v1/apps/{token}/tables", {"page_size": 100})
            tables = list(data.get("items") or [])[:MAX_BITABLE_TABLES]
        lines = [self._markdown_header("飞书多维表格", title, reference.source_url).rstrip()]
        total = 0
        truncated = False
        for table in tables:
            table_id = str(table.get("table_id") or "")
            if not table_id:
                continue
            table_name = str(table.get("name") or table_id)
            lines.extend(("", f"## 数据表：{table_name}", ""))
            page_token = ""
            table_rows = 0
            while total < MAX_BITABLE_RECORDS:
                params = {"page_size": min(500, MAX_BITABLE_RECORDS - total)}
                if page_token:
                    params["page_token"] = page_token
                if reference.view_id:
                    params["view_id"] = reference.view_id
                data = self.api.get(
                    f"bitable/v1/apps/{token}/tables/{quote(table_id, safe='')}/records",
                    params,
                )
                items = list(data.get("items") or [])
                for item in items:
                    total += 1
                    table_rows += 1
                    fields = item.get("fields") or {}
                    lines.append(f"### 记录 {table_rows}")
                    for name, value in fields.items():
                        lines.append(f"- {name}: {self._render_value(value)}")
                    lines.append("")
                    if total >= MAX_BITABLE_RECORDS:
                        break
                if not data.get("has_more") or not data.get("page_token") or total >= MAX_BITABLE_RECORDS:
                    truncated = bool(data.get("has_more")) or total >= MAX_BITABLE_RECORDS
                    break
                page_token = str(data.get("page_token"))
        if total == 0:
            lines.append("（没有可读取的记录）")
        if truncated or (not reference.table_id and len(tables) >= MAX_BITABLE_TABLES):
            lines.append("\n> 为保证响应速度，本次快照只读取前 500 条记录、最多 8 个数据表。")
        return title, "\n".join(lines)

    def _read_calendar(self, reference):
        start, end = self._calendar_range(reference)
        if reference.calendar_id:
            calendars = [
                {
                    "calendar_id": reference.calendar_id,
                    "summary": reference.calendar_id,
                }
            ]
        else:
            data = self.api.get("calendar/v4/calendars", {"page_size": 50})
            calendars = [
                item for item in list(data.get("calendar_list") or [])
                if not item.get("is_deleted")
            ][:MAX_CALENDARS]
        title = f"飞书日历 {start.date().isoformat()} 至 {end.date().isoformat()}"
        lines = [self._markdown_header("飞书日历", title, reference.source_url).rstrip()]
        total = 0
        for calendar in calendars:
            calendar_id = str(calendar.get("calendar_id") or "")
            if not calendar_id:
                continue
            calendar_name = str(calendar.get("summary_alias") or calendar.get("summary") or calendar_id)
            lines.extend(("", f"## 日历：{calendar_name}", ""))
            if reference.event_id:
                event_data = self.api.get(
                    f"calendar/v4/calendars/{quote(calendar_id, safe='')}/events/"
                    f"{quote(reference.event_id, safe='')}"
                )
                events = [event_data.get("event") or event_data]
            else:
                data = self.api.get(
                    f"calendar/v4/calendars/{quote(calendar_id, safe='')}/events",
                    {
                        "start_time": int(start.timestamp()),
                        "end_time": int(end.timestamp()),
                        "page_size": 500,
                    },
                )
                events = list(data.get("items") or data.get("event_list") or [])
            for event in events:
                if not event or total >= MAX_CALENDAR_EVENTS:
                    break
                total += 1
                lines.extend(self._render_event(event))
        if not calendars:
            lines.append("\n（应用身份下没有可见日历。请把目标日历共享给 Poppy 应用。）")
        elif total == 0:
            lines.append("\n（该时间范围内没有可读取的日程。）")
        if total >= MAX_CALENDAR_EVENTS:
            lines.append("\n> 为保证响应速度，本次快照只读取前 200 个日程。")
        return title, "\n".join(lines)

    def _persist(self, reference, session_id, title, content):
        content = str(content or "")
        truncated = len(content) > MAX_SNAPSHOT_CHARS
        if truncated:
            content = content[:MAX_SNAPSHOT_CHARS] + "\n\n> 内容过长，快照已截断。"
        session_folder = self.cache_root / "cloud" / self._safe_name(session_id, fallback="session")
        session_folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(reference.cache_key.encode("utf-8")).hexdigest()[:12]
        filename = f"{reference.kind}-{digest}-{self._safe_name(title, fallback=reference.kind)}.md"
        path = session_folder / filename[:220]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return CloudSnapshot(reference.kind, title, reference.source_url, str(path.resolve()), truncated)

    def _friendly_error(self, reference, error):
        kind_names = {
            "docx": "云文档",
            "wiki": "知识库",
            "bitable": "多维表格",
            "calendar": "日历",
        }
        scope_names = {
            "docx": "查看新版文档",
            "wiki": "查看知识库 / 查看知识空间节点信息",
            "bitable": "查看、评论和导出多维表格",
            "calendar": "读取日历信息（或获取日历、日程及忙闲信息）",
        }
        kind = kind_names.get(reference.kind, "云资源")
        if error.status == 403 or error.code in {
            131006, 1770032, 1254302, 191002, 99991672, 99991679,
        }:
            detail = (
                f"飞书拒绝读取这个{kind}。请先在开放平台开通“{scope_names.get(reference.kind, '只读')}”权限、"
                "发布应用新版本，并把具体资源共享给 Poppy 应用（知识库需把应用加入成员，"
                "多维表格需设为协作者，日历需共享给应用身份）。"
            )
        else:
            detail = f"读取飞书{kind}失败：{error}"
        return FeishuCloudError(detail, code=error.code, resource_kind=reference.kind)

    def _calendar_range(self, reference):
        now = self.now()
        try:
            start = (
                datetime.strptime(reference.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if reference.start_date
                else now
            )
            end = (
                datetime.strptime(reference.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                + timedelta(days=1)
                if reference.end_date
                else start + timedelta(days=14)
            )
        except ValueError:
            raise FeishuCloudError("日历日期格式应为 YYYY-MM-DD。", resource_kind="calendar") from None
        if end <= start:
            raise FeishuCloudError("日历结束日期必须晚于开始日期。", resource_kind="calendar")
        if end - start > timedelta(days=366):
            end = start + timedelta(days=366)
        return start, end

    @staticmethod
    def _render_event(event):
        summary = str(event.get("summary") or "未命名日程")
        start = FeishuCloudReader._event_time(event.get("start_time") or {})
        end = FeishuCloudReader._event_time(event.get("end_time") or {})
        description = str(event.get("description") or "").strip()
        location = event.get("location") or {}
        location_name = str(location.get("name") or location.get("address") or "").strip()
        lines = [f"### {summary}", f"- 时间：{start} — {end}"]
        if location_name:
            lines.append(f"- 地点：{location_name}")
        if description:
            lines.append(f"- 说明：{description}")
        lines.append("")
        return lines

    @staticmethod
    def _event_time(value):
        if value.get("date"):
            return str(value.get("date"))
        timestamp = str(value.get("timestamp") or "")
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone().isoformat(
                timespec="minutes"
            )
        except (TypeError, ValueError, OSError):
            return timestamp or "未知"

    @staticmethod
    def _markdown_header(kind, title, source_url):
        source = f"\n- 原始链接：{source_url}" if source_url else ""
        return (
            f"# {title}\n\n"
            f"- 类型：{kind}{source}\n"
            f"- 获取方式：Poppy 使用飞书应用身份只读 OpenAPI 生成会话级快照\n\n"
        )

    @staticmethod
    def _render_value(value):
        if isinstance(value, str):
            return value.replace("\n", " ").strip()
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _safe_name(value, fallback="resource"):
        name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", str(value or "")).strip(" ._")
        name = re.sub(r"\s+", " ", name)[:100]
        return name or fallback

    @staticmethod
    def _first(query, *names):
        for name in names:
            values = query.get(name) or []
            if values:
                return str(values[0])
        return ""
