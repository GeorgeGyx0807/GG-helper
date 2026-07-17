"""Feishu/Lark channel adapter for the local Poppy desktop runtime.

The adapter deliberately keeps Feishu as an I/O channel.  Sessions, model
runs, document indexing, grants, and cancellation remain owned by
``DesktopController`` so the desktop and Feishu clients share one authority
boundary instead of growing two agent runtimes.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import secrets
import string
import threading
import time
from pathlib import Path
from urllib.parse import quote

from ..features.document_extractors import DOCUMENT_EXTENSIONS
from ..features.document_index import MAX_DOCUMENT_FILE_BYTES, MAX_FILE_BYTES, SUPPORTED_EXTENSIONS
from .feishu_cloud import (
    CALENDAR_COMMAND_PATTERN,
    READ_SCOPE_IDS,
    FeishuCloudError,
    FeishuCloudReader,
)


CHANNEL_NAME = "feishu"
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}
PAIR_COMMANDS = ("绑定", "/bind")
NEW_SESSION_COMMANDS = {"新对话", "/new", "/reset"}
FILE_PLACEHOLDER_PATTERN = re.compile(r"<(?:(?:file|image|audio|video)\b)[^>]*>", re.I)
SAFE_PATH_FRAGMENT = re.compile(r"[^A-Za-z0-9_.-]+")

DEFAULTS = {
    "feishu_enabled": False,
    "feishu_app_id": "",
    "feishu_allowed_users": [],
    "feishu_allowed_chats": [],
    "feishu_require_mention": True,
    "feishu_cloud_enabled": True,
    "feishu_workspace_root": "",
    "feishu_max_file_mb": 50,
    "feishu_pairing_code": "",
}


class FeishuBridge:
    """Own the Feishu connection and bridge inbound messages to Poppy runs."""

    def __init__(self, controller, channel_factory=None, cloud_reader_factory=None):
        self.controller = controller
        self.database = controller.database
        self.paths = controller.paths
        self.channel_factory = channel_factory
        self.cloud_reader_factory = cloud_reader_factory
        self._lock = threading.RLock()
        self._channel = None
        self._thread = None
        self._stop_event = threading.Event()
        self._generation = 0
        self._state = "disabled"
        self._error = ""
        self._connected_at = ""
        self._bot_name = ""
        self._bot_open_id = ""
        self._cloud_reader_instance = None
        self._cloud_reader_signature = None

    def settings(self):
        stored = self.database.get_settings()
        values = {key: stored.get(key, default) for key, default in DEFAULTS.items()}
        if not values["feishu_pairing_code"]:
            values["feishu_pairing_code"] = self._new_pairing_code()
            self.database.set_setting("feishu_pairing_code", values["feishu_pairing_code"])
        values["feishu_allowed_users"] = self._string_list(values["feishu_allowed_users"])
        values["feishu_allowed_chats"] = self._string_list(values["feishu_allowed_chats"])
        values["feishu_secret_configured"] = bool(os.environ.get("POPPY_FEISHU_APP_SECRET", "").strip())
        values["feishu_cloud_scope_ids"] = list(READ_SCOPE_IDS)
        values["feishu_cloud_permission_url"] = (
            "https://open.feishu.cn/app/"
            + quote(str(values["feishu_app_id"] or ""), safe="")
            + "/auth?q="
            + quote(",".join(READ_SCOPE_IDS), safe=",:")
            + "&op_from=openapi&token_type=tenant"
            if values["feishu_app_id"]
            else ""
        )
        with self._lock:
            values.update(
                {
                    "feishu_status": self._state,
                    "feishu_error": self._error,
                    "feishu_connected_at": self._connected_at,
                    "feishu_bot_name": self._bot_name,
                    "feishu_bot_open_id": self._bot_open_id,
                }
            )
        values["feishu_sessions"] = self.database.list_channel_sessions(CHANNEL_NAME)
        return values

    def update_settings(self, values):
        allowed = set(DEFAULTS) - {"feishu_pairing_code"}
        for key, value in values.items():
            if key not in allowed:
                continue
            if key in {"feishu_allowed_users", "feishu_allowed_chats"}:
                value = self._string_list(value)
            elif key in {"feishu_enabled", "feishu_require_mention", "feishu_cloud_enabled"}:
                value = bool(value)
            elif key == "feishu_max_file_mb":
                value = max(1, min(int(value), 50))
            elif key == "feishu_app_id":
                value = str(value or "").strip()[:160]
                if value and not value.startswith("cli_"):
                    raise ValueError("飞书 App ID 应以 cli_ 开头")
            elif key == "feishu_workspace_root":
                value = str(value or "").strip()
                if value:
                    grant = self.controller._grant_covering_path(Path(value).expanduser().resolve())
                    if grant is None:
                        raise PermissionError("飞书项目目录必须先在 Poppy 中授权")
                    value = grant["path"]
            self.database.set_setting(key, value)
        self.restart()
        return self.settings()

    def start(self):
        config = self.settings()
        if not config["feishu_enabled"]:
            self._set_state("disabled")
            return self.settings()
        if not config["feishu_app_id"] or not config["feishu_secret_configured"]:
            self._set_state("not_configured", "请先填写 App ID 并保存 App Secret")
            return self.settings()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.settings()
            self._generation += 1
            generation = self._generation
            self._stop_event = threading.Event()
            self._state = "connecting"
            self._error = ""
            self._thread = threading.Thread(
                target=self._thread_main,
                args=(generation, config),
                name="poppy-feishu",
                daemon=True,
            )
            self._thread.start()
        return self.settings()

    def stop(self):
        with self._lock:
            self._generation += 1
            self._stop_event.set()
            channel = self._channel
            thread = self._thread
        if channel is not None:
            try:
                channel.stop()
            except Exception:
                pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=4.0)
        with self._lock:
            self._channel = None
            self._thread = None
            if self._state != "disabled":
                self._state = "stopped"

    def restart(self):
        self.stop()
        return self.start()

    def delete_mapping(self, mapping_id):
        self.database.delete_channel_session(mapping_id)
        return self.settings()

    def _thread_main(self, generation, config):
        try:
            # lark-channel-sdk creates its WebSocket event loop while its
            # modules are imported. Build the channel before asyncio.run()
            # starts Poppy's orchestration loop, otherwise the SDK can bind
            # to that running loop and stop it during disconnect/reconnect.
            channel = self._build_channel(config)
            asyncio.run(self._serve(generation, config, channel))
        except Exception as exc:
            if self._is_current(generation):
                self._set_state("error", self._safe_error(exc))
        finally:
            with self._lock:
                if generation == self._generation:
                    self._channel = None
                    self._thread = None
                    if self._state not in {"error", "disabled", "not_configured"}:
                        self._state = "stopped"

    async def _serve(self, generation, config, channel):
        channel.on("message", self._on_message)
        channel.on("reconnecting", lambda: self._set_state("reconnecting"))
        channel.on("reconnected", lambda: self._set_state("connected"))
        channel.on("error", lambda error: self._set_state("error", self._safe_error(error)))
        with self._lock:
            if generation != self._generation:
                return
            self._channel = channel
        await channel.connect_until_ready(timeout=20.0)
        identity = getattr(channel, "bot_identity", None)
        with self._lock:
            if generation != self._generation:
                await channel.disconnect()
                return
            self._state = "connected"
            self._error = ""
            self._connected_at = self._utc_display()
            self._bot_name = str(getattr(identity, "name", "") or "")
            self._bot_open_id = str(getattr(identity, "open_id", "") or "")
        while self._is_current(generation) and not self._stop_event.is_set():
            await asyncio.sleep(0.25)
        await channel.disconnect()

    def _build_channel(self, config):
        if self.channel_factory is not None:
            return self.channel_factory(config)
        from lark_channel import (
            FeishuChannel,
            InboundConfig,
            LogLevel,
            PolicyConfig,
            SecurityConfig,
        )

        max_bytes = int(config["feishu_max_file_mb"]) * 1024 * 1024
        return FeishuChannel(
            app_id=config["feishu_app_id"],
            app_secret=os.environ["POPPY_FEISHU_APP_SECRET"],
            log_level=LogLevel.WARNING,
            transport="ws",
            policy=PolicyConfig(
                dm_policy="open",
                group_policy="open",
                require_mention=bool(config["feishu_require_mention"]),
                respond_to_mention_all=False,
            ),
            inbound=InboundConfig(media_max_mb=int(config["feishu_max_file_mb"])),
            security=SecurityConfig(
                mode="strict",
                resource_overflow_policy="drop",
                max_concurrent_ws_handlers=8,
            ),
            config=self._channel_config_with_cache(config, max_bytes),
        )

    def _channel_config_with_cache(self, config, max_bytes):
        from lark_channel import ChannelConfig, MediaCacheConfig

        channel_config = ChannelConfig()
        channel_config.app_id = config["feishu_app_id"]
        channel_config.app_secret = os.environ["POPPY_FEISHU_APP_SECRET"]
        channel_config.media_cache = MediaCacheConfig(
            enabled=True,
            root_dir=self.paths.feishu_attachments,
            ttl_seconds=30 * 24 * 3600,
            max_entries=256,
            max_bytes=512 * 1024 * 1024,
            max_file_bytes=max_bytes,
        )
        return channel_config

    async def _on_message(self, message):
        message_id = str(getattr(message, "message_id", "") or "")
        if not message_id or not self.database.claim_channel_message(CHANNEL_NAME, message_id):
            return
        try:
            await self._process_message(message)
        except Exception as exc:
            self.database.finish_channel_message(CHANNEL_NAME, message_id, "failed")
            await self._send_error(message, exc)
        else:
            self.database.finish_channel_message(CHANNEL_NAME, message_id, "completed")

    async def _process_message(self, message):
        channel = self._channel
        if channel is None:
            return
        text = self._message_text(message)
        chat_type = str(getattr(message, "chat_type", "unknown") or "unknown")
        chat_id = str(getattr(message, "chat_id", "") or "")
        sender_id = str(getattr(message, "sender_id", "") or "")
        if not chat_id or not sender_id:
            return
        config = self.settings()

        if sender_id not in config["feishu_allowed_users"]:
            if chat_type == "p2p" and self._is_pair_command(text, config["feishu_pairing_code"]):
                users = sorted(set(config["feishu_allowed_users"] + [sender_id]))
                self.database.set_setting("feishu_allowed_users", users)
                self.database.set_setting("feishu_pairing_code", self._new_pairing_code())
                self.database.add_audit_event(
                    "feishu_user_paired", scope="feishu", details={"sender": self._short_id(sender_id)}
                )
                await channel.send(
                    chat_id,
                    {"text": "绑定成功。现在可以直接向 Poppy 提问或发送文档。"},
                    self._reply_options(message),
                )
            elif chat_type == "p2p":
                await channel.send(
                    chat_id,
                    {"text": "这个 Poppy 尚未与您的飞书账号绑定。请在 Mac 的 Poppy 设置中查看绑定码，然后发送：绑定 绑定码"},
                    self._reply_options(message),
                )
            return

        if chat_type != "p2p":
            if chat_id not in config["feishu_allowed_chats"]:
                return
            if config["feishu_require_mention"] and not bool(getattr(message, "mentioned_bot", False)):
                return

        new_session = text.strip().lower() in NEW_SESSION_COMMANDS
        mapping = self._get_or_create_session(message, config, force_new=new_session)
        if new_session:
            await channel.send(
                chat_id,
                {"text": "已开始新的 Poppy 对话。"},
                self._reply_options(message),
            )
            return

        attachments, rejected = await self._download_attachments(message, config)
        clean_text = FILE_PLACEHOLDER_PATTERN.sub("", text).strip()
        cloud_snapshots = []
        cloud_reader = self._cloud_reader(config)
        cloud_references = cloud_reader.parse_references(clean_text)
        if cloud_references and not config["feishu_cloud_enabled"]:
            await channel.send(
                chat_id,
                {"text": "Poppy 的飞书云内容读取已关闭。请在 Mac 设置 → 飞书接入中开启后重试。"},
                self._reply_options(message),
            )
            return
        if cloud_references:
            try:
                cloud_snapshots = await asyncio.to_thread(
                    cloud_reader.read_message,
                    clean_text,
                    mapping["poppy_session_id"],
                )
            except FeishuCloudError as exc:
                self.database.add_audit_event(
                    "feishu_cloud_read_failed",
                    scope="feishu",
                    details={
                        "kind": exc.resource_kind,
                        "code": exc.code,
                        "error": self._safe_error(exc),
                    },
                )
                await channel.send(
                    chat_id,
                    {"text": str(exc)},
                    self._reply_options(message),
                )
                return
            attachments.extend(snapshot.path for snapshot in cloud_snapshots)
            self.database.add_audit_event(
                "feishu_cloud_read",
                scope="feishu",
                details={
                    "kinds": sorted({item.kind for item in cloud_snapshots}),
                    "count": len(cloud_snapshots),
                    "session": mapping["poppy_session_id"],
                },
            )
        if attachments:
            self.controller.register_channel_attachments(mapping["poppy_session_id"], attachments)

        if cloud_snapshots:
            clean_text = cloud_reader.strip_resource_urls(clean_text)
            if CALENDAR_COMMAND_PATTERN.search(clean_text):
                clean_text = CALENDAR_COMMAND_PATTERN.sub(
                    "请按时间顺序列出并概括这些飞书日历日程。 ",
                    clean_text,
                    count=1,
                ).strip()
        if not clean_text:
            if attachments:
                names = "、".join(item.title for item in cloud_snapshots) or "、".join(
                    Path(path).name for path in attachments
                )
                suffix = f"；另有 {len(rejected)} 个文件未能读取" if rejected else ""
                await channel.send(
                    chat_id,
                    {"text": f"已读取并索引：{names}{suffix}。请继续发送你想问的问题。"},
                    self._reply_options(message),
                )
            elif rejected:
                await channel.send(
                    chat_id,
                    {"text": "没有可读取的附件。Poppy 当前支持 PDF、Word、Excel、PowerPoint 和常见文本文件，单个文件不超过 50 MB。"},
                    self._reply_options(message),
                )
            return

        if rejected:
            clean_text += "\n\n注意：本条消息中有附件未通过格式或大小检查，请不要假设已读取这些附件。"
        run = self.controller.start_channel_run(mapping["poppy_session_id"], clean_text)
        await self._reply_with_run(message, run["run_id"])

    def _cloud_reader(self, config):
        app_id = str(config.get("feishu_app_id") or "")
        app_secret = str(os.environ.get("POPPY_FEISHU_APP_SECRET") or "")
        signature = (app_id, app_secret)
        with self._lock:
            if self._cloud_reader_instance is not None and self._cloud_reader_signature == signature:
                return self._cloud_reader_instance
            if self.cloud_reader_factory is not None:
                reader = self.cloud_reader_factory(config)
            else:
                reader = FeishuCloudReader(
                    app_id,
                    app_secret,
                    self.paths.feishu_attachments,
                )
            self._cloud_reader_instance = reader
            self._cloud_reader_signature = signature
            return reader

    def _get_or_create_session(self, message, config, force_new=False):
        chat_type = str(getattr(message, "chat_type", "unknown") or "unknown")
        chat_id = str(getattr(message, "chat_id", "") or "")
        sender_id = str(getattr(message, "sender_id", "") or "")
        conversation = getattr(message, "conversation", None)
        thread_id = str(getattr(conversation, "thread_id", "") or "") if chat_type != "p2p" else ""
        tenant_key = self._tenant_key(message, config["feishu_app_id"])
        mapping_sender = sender_id if chat_type == "p2p" else ""
        workspace_root = str(config["feishu_workspace_root"] or "")
        mapping = self.database.get_channel_session(
            CHANNEL_NAME, tenant_key, chat_id, thread_id, mapping_sender
        )
        if mapping and not force_new and mapping.get("workspace_root", "") == workspace_root:
            if self.database.get_session(mapping["poppy_session_id"]):
                return mapping

        sender_name = str(getattr(message, "sender_name", "") or "飞书")[:40]
        if workspace_root:
            session = self.controller.create_session(
                workspace_root, f"飞书 · {sender_name}", session_type="project"
            )
        else:
            session = self.controller.create_session("", f"飞书 · {sender_name}", session_type="chat")
        return self.database.upsert_channel_session(
            CHANNEL_NAME,
            tenant_key,
            chat_id,
            session["id"],
            thread_id=thread_id,
            sender_open_id=mapping_sender,
            workspace_root=workspace_root,
        )

    async def _download_attachments(self, message, config):
        channel = self._channel
        if channel is None:
            return [], []
        sources = list(getattr(message, "batched_sources", None) or [message])
        attachments = []
        rejected = []
        for source in sources:
            source_id = str(getattr(source, "message_id", "") or "")
            for resource in list(getattr(source, "resources", None) or []):
                if str(getattr(resource, "type", "")) != "file":
                    rejected.append(str(getattr(resource, "file_name", "") or "图片/媒体"))
                    continue
                name = str(getattr(resource, "file_name", "") or "")
                extension = Path(name).suffix.lower()
                if extension not in SUPPORTED_EXTENSIONS:
                    rejected.append(name or "未知文件")
                    continue
                folder = self.paths.feishu_attachments / self._safe_fragment(source_id)
                try:
                    path = await channel.download_resource_to_file(
                        str(getattr(resource, "file_key", "") or ""),
                        resource_type="file",
                        message_id=source_id,
                        dest_dir=folder,
                        file_name=name,
                    )
                    self._validate_download(path, int(config["feishu_max_file_mb"]))
                except Exception:
                    rejected.append(name or "未知文件")
                    continue
                attachments.append(str(Path(path).resolve()))
        return sorted(set(attachments)), rejected

    def _validate_download(self, path, max_file_mb):
        path = Path(path).resolve()
        try:
            path.relative_to(self.paths.feishu_attachments.resolve())
        except ValueError as exc:
            raise PermissionError("飞书附件写入了非预期目录") from exc
        extension = path.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            path.unlink(missing_ok=True)
            raise ValueError("附件格式不受支持")
        size_limit = min(max_file_mb * 1024 * 1024, MAX_DOCUMENT_FILE_BYTES)
        if extension not in DOCUMENT_EXTENSIONS:
            size_limit = min(size_limit, MAX_FILE_BYTES)
        if path.stat().st_size > size_limit:
            path.unlink(missing_ok=True)
            raise ValueError("附件超过 Poppy 的解析上限")
        with path.open("rb") as stream:
            header = stream.read(4096)
        if extension == ".pdf" and not header.startswith(b"%PDF-"):
            path.unlink(missing_ok=True)
            raise ValueError("PDF 文件头无效")
        if extension in {".docx", ".pptx", ".xlsx"} and not header.startswith(b"PK"):
            path.unlink(missing_ok=True)
            raise ValueError("Office 文件头无效")
        if extension == ".xls" and not header.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            path.unlink(missing_ok=True)
            raise ValueError("Excel 文件头无效")
        if extension not in DOCUMENT_EXTENSIONS and b"\x00" in header:
            path.unlink(missing_ok=True)
            raise ValueError("文本附件包含二进制内容")

    async def _reply_with_run(self, message, run_id):
        channel = self._channel
        if channel is None:
            return

        async def producer(controller):
            await controller.set_content("Poppy 正在阅读和思考…")
            sequence = 0
            answer = ""
            last_sent = ""
            last_update = 0.0
            while True:
                for event in self.controller.get_events(run_id, after_sequence=sequence):
                    sequence = max(sequence, int(event.get("sequence", 0)))
                    payload = event.get("payload") or {}
                    if event.get("event_type") == "message.delta":
                        answer += str(payload.get("delta") or "")
                    elif event.get("event_type") == "message.completed":
                        answer = str(payload.get("content") or answer)
                snapshot = self.controller.get_run(run_id)
                now = time.monotonic()
                if answer and answer != last_sent and (now - last_update >= 0.8 or snapshot["status"] in TERMINAL_STATUSES):
                    await controller.set_content(answer)
                    last_sent = answer
                    last_update = now
                if snapshot["status"] in TERMINAL_STATUSES:
                    if snapshot["status"] == "failed":
                        await controller.set_content("Poppy 处理失败，请在 Mac 上查看连接和模型设置后重试。")
                    elif snapshot["status"] == "cancelled":
                        await controller.set_content("本次 Poppy 任务已停止。")
                    elif not answer:
                        await controller.set_content(str(snapshot.get("answer") or "Poppy 已完成，但没有生成可显示的回答。"))
                    return
                await asyncio.sleep(0.16)

        try:
            await channel.stream(
                str(getattr(message, "chat_id", "")),
                {"markdown": producer},
                self._reply_options(message),
            )
        except Exception:
            snapshot = await self._wait_for_run(run_id)
            if snapshot["status"] == "completed":
                text = str(snapshot.get("answer") or "Poppy 已完成，但没有生成可显示的回答。")
            elif snapshot["status"] == "cancelled":
                text = "本次 Poppy 任务已停止。"
            else:
                text = "Poppy 处理失败，请在 Mac 上查看连接和模型设置后重试。"
            await channel.send(
                str(getattr(message, "chat_id", "")),
                {"markdown": text},
                self._reply_options(message),
            )

    async def _wait_for_run(self, run_id):
        while True:
            snapshot = self.controller.get_run(run_id)
            if snapshot["status"] in TERMINAL_STATUSES:
                return snapshot
            await asyncio.sleep(0.2)

    async def _send_error(self, message, error):
        channel = self._channel
        if channel is None:
            return
        self.database.add_audit_event(
            "feishu_message_failed", scope="feishu", details={"error": self._safe_error(error)}
        )
        try:
            await channel.send(
                str(getattr(message, "chat_id", "")),
                {"text": "Poppy 暂时无法处理这条消息。请检查文件格式、模型连接或稍后重试。"},
                self._reply_options(message),
            )
        except Exception:
            pass

    @staticmethod
    def _reply_options(message):
        chat_type = str(getattr(message, "chat_type", "") or "")
        return {
            "reply_to": str(getattr(message, "message_id", "") or ""),
            "reply_in_thread": chat_type != "p2p",
        }

    @staticmethod
    def _message_text(message):
        text = str(
            getattr(message, "safe_content_text", "")
            or getattr(message, "content_text", "")
            or ""
        )
        for mention in list(getattr(message, "mentions", None) or []):
            key = str(getattr(mention, "key", "") or "")
            if key:
                text = text.replace(key, "")
        return text.strip()[:20_000]

    @staticmethod
    def _tenant_key(message, fallback):
        raw = getattr(message, "raw", None) or {}
        if isinstance(raw, dict):
            header = raw.get("header") or {}
            if isinstance(header, dict) and header.get("tenant_key"):
                return str(header["tenant_key"])
            if raw.get("tenant_key"):
                return str(raw["tenant_key"])
        return str(fallback or "default")

    @staticmethod
    def _is_pair_command(text, code):
        normalized = " ".join(str(text or "").strip().split())
        for command in PAIR_COMMANDS:
            prefix = command + " "
            if normalized.startswith(prefix):
                return hmac.compare_digest(normalized[len(prefix):].strip(), str(code or ""))
        return False

    @staticmethod
    def _new_pairing_code():
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(6))

    @staticmethod
    def _string_list(value):
        if isinstance(value, str):
            value = re.split(r"[,，\s]+", value)
        if not isinstance(value, (list, tuple, set)):
            return []
        return sorted({str(item).strip() for item in value if str(item).strip()})

    @staticmethod
    def _safe_fragment(value):
        cleaned = SAFE_PATH_FRAGMENT.sub("_", str(value or ""))[:160]
        return cleaned or secrets.token_hex(8)

    @staticmethod
    def _short_id(value):
        value = str(value or "")
        return value[:6] + "…" + value[-4:] if len(value) > 12 else value

    @staticmethod
    def _safe_error(error):
        text = str(error or "飞书连接失败")
        text = re.sub(r"(?i)(app[_ -]?secret|token|authorization)\s*[:=]\s*\S+", r"\1=[已隐藏]", text)
        return text[:500]

    @staticmethod
    def _utc_display():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _is_current(self, generation):
        with self._lock:
            return generation == self._generation

    def _set_state(self, state, error=""):
        with self._lock:
            self._state = str(state)
            self._error = str(error or "")[:500]
