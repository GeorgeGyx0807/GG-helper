import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from poppy.integrations.feishu import FeishuBridge
from poppy.integrations.feishu_cloud import CloudSnapshot, FeishuCloudReader
from poppy.storage import AppPaths, DesktopDatabase


class FakeRunController:
    def __init__(self, paths):
        self.paths = paths
        self.database = DesktopDatabase(paths.database)
        self.runs = []
        self.attachments = []

    def _grant_covering_path(self, _path):
        return None

    def create_session(self, workspace_root="", title="新对话", session_type="chat"):
        return self.database.upsert_session(
            "session_" + uuid4().hex,
            title,
            workspace_root,
            session_type=session_type,
        )

    def register_channel_attachments(self, session_id, attachments):
        self.attachments.append((session_id, list(attachments)))

    def start_channel_run(self, session_id, message):
        self.runs.append((session_id, message))
        return {"run_id": "run_test"}

    def get_events(self, _run_id, after_sequence=0):
        if after_sequence:
            return []
        return [
            {
                "sequence": 1,
                "event_type": "message.completed",
                "payload": {"content": "这是来自 Poppy 的回答。"},
            }
        ]

    def get_run(self, _run_id):
        return {"status": "completed", "answer": "这是来自 Poppy 的回答。"}


class FakeStreamController:
    def __init__(self):
        self.values = []

    async def set_content(self, value):
        self.values.append(value)


class FakeFeishuChannel:
    def __init__(self, paths):
        self.paths = paths
        self.sent = []
        self.streamed = []

    async def send(self, to, message, opts=None):
        self.sent.append((to, message, opts or {}))

    async def stream(self, to, spec, opts=None):
        controller = FakeStreamController()
        await spec["markdown"](controller)
        self.streamed.append((to, controller.values, opts or {}))

    async def download_resource_to_file(
        self, _file_key, *, resource_type, message_id, dest_dir, file_name
    ):
        assert resource_type == "file"
        assert message_id
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / Path(file_name).name
        target.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return target


class FakeLifecycleChannel:
    def __init__(self):
        self.handlers = {}
        self.bot_identity = SimpleNamespace(name="Poppy", open_id="ou_bot")
        self.disconnected = False

    def on(self, event, handler):
        self.handlers[event] = handler

    async def connect_until_ready(self, timeout):
        assert timeout == 20.0

    async def disconnect(self):
        self.disconnected = True


def inbound_message(message_id, text, *, resources=None, sender_id="ou_allowed"):
    return SimpleNamespace(
        message_id=message_id,
        chat_id="oc_private",
        chat_type="p2p",
        sender_id=sender_id,
        sender_name="测试用户",
        safe_content_text=text,
        content_text=text,
        mentions=[],
        mentioned_bot=False,
        conversation=SimpleNamespace(thread_id=""),
        resources=list(resources or []),
        batched_sources=None,
        raw={"header": {"tenant_key": "tenant_test"}},
    )


def test_feishu_pairing_rotates_code_and_persists_allowed_user(tmp_path):
    controller = FakeRunController(AppPaths(tmp_path / "data").ensure())
    bridge = FeishuBridge(controller)
    channel = FakeFeishuChannel(controller.paths)
    bridge._channel = channel
    code = bridge.settings()["feishu_pairing_code"]

    asyncio.run(bridge._on_message(inbound_message("om_pair", f"绑定 {code}", sender_id="ou_new")))

    settings = bridge.settings()
    assert settings["feishu_allowed_users"] == ["ou_new"]
    assert settings["feishu_pairing_code"] != code
    assert channel.sent[0][1]["text"].startswith("绑定成功")


def test_feishu_attachment_enters_session_and_answer_streams_once(tmp_path):
    controller = FakeRunController(AppPaths(tmp_path / "data").ensure())
    controller.database.set_setting("feishu_allowed_users", ["ou_allowed"])
    bridge = FeishuBridge(controller)
    channel = FakeFeishuChannel(controller.paths)
    bridge._channel = channel
    resource = SimpleNamespace(type="file", file_key="file_key", file_name="paper.pdf")
    message = inbound_message("om_file", '<file key="file_key" name="paper.pdf"/> 请总结这篇文献', resources=[resource])

    asyncio.run(bridge._on_message(message))
    asyncio.run(bridge._on_message(message))

    assert len(controller.attachments) == 1
    assert controller.attachments[0][1][0].endswith("paper.pdf")
    assert controller.runs[0][1] == "请总结这篇文献"
    assert len(controller.runs) == 1
    assert channel.streamed[0][1][-1] == "这是来自 Poppy 的回答。"
    mappings = controller.database.list_channel_sessions("feishu")
    assert len(mappings) == 1
    assert mappings[0]["poppy_session_id"] == controller.runs[0][0]


def test_feishu_group_requires_both_allowlist_and_mention(tmp_path):
    controller = FakeRunController(AppPaths(tmp_path / "data").ensure())
    controller.database.set_setting("feishu_allowed_users", ["ou_allowed"])
    controller.database.set_setting("feishu_allowed_chats", ["oc_group"])
    controller.database.set_setting("feishu_require_mention", True)
    bridge = FeishuBridge(controller)
    bridge._channel = FakeFeishuChannel(controller.paths)
    message = inbound_message("om_group", "不要响应")
    message.chat_type = "group"
    message.chat_id = "oc_group"

    asyncio.run(bridge._on_message(message))

    assert controller.runs == []


def test_channel_message_and_attachment_records_are_persistent(tmp_path):
    paths = AppPaths(tmp_path / "data").ensure()
    database = DesktopDatabase(paths.database)
    session = database.upsert_session("session_test", "飞书", "", session_type="chat")
    mapping = database.upsert_channel_session(
        "feishu", "tenant", "chat", session["id"], sender_open_id="user"
    )
    attachment = paths.feishu_attachments / "paper.pdf"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_bytes(b"%PDF-1.4\n%%EOF\n")
    database.add_session_attachment(session["id"], attachment, source="feishu")

    assert database.claim_channel_message("feishu", "message") is True
    assert database.claim_channel_message("feishu", "message") is False
    assert database.list_session_attachments(session["id"])[0]["path"] == str(attachment.resolve())
    assert database.get_channel_session("feishu", "tenant", "chat", "", "user")["id"] == mapping["id"]


def test_feishu_sdk_is_built_before_poppy_asyncio_loop_starts(tmp_path):
    controller = FakeRunController(AppPaths(tmp_path / "data").ensure())
    lifecycle = FakeLifecycleChannel()
    factory_running_loops = []

    def channel_factory(_config):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            factory_running_loops.append(False)
        else:
            factory_running_loops.append(True)
        return lifecycle

    bridge = FeishuBridge(controller, channel_factory=channel_factory)
    bridge._stop_event.set()
    bridge._thread_main(bridge._generation, bridge.settings())

    assert factory_running_loops == [False]
    assert lifecycle.disconnected is True


def test_feishu_cloud_link_is_indexed_in_current_session_before_question(tmp_path):
    controller = FakeRunController(AppPaths(tmp_path / "data").ensure())
    controller.database.set_setting("feishu_allowed_users", ["ou_allowed"])

    class FakeCloudReader:
        parse_references = staticmethod(FeishuCloudReader.parse_references)
        strip_resource_urls = staticmethod(FeishuCloudReader.strip_resource_urls)

        def read_message(self, _text, session_id):
            folder = controller.paths.feishu_attachments / "cloud" / session_id
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "研究计划.md"
            path.write_text("# 研究计划\n正文", encoding="utf-8")
            return [CloudSnapshot("docx", "研究计划", "https://tenant.feishu.cn/docx/doc_1", str(path))]

    bridge = FeishuBridge(controller, cloud_reader_factory=lambda _config: FakeCloudReader())
    bridge._channel = FakeFeishuChannel(controller.paths)
    message = inbound_message(
        "om_cloud",
        "请总结 https://tenant.feishu.cn/docx/doc_1",
    )

    asyncio.run(bridge._on_message(message))

    assert len(controller.attachments) == 1
    session_id, attachments = controller.attachments[0]
    assert session_id == controller.runs[0][0]
    assert attachments[0].endswith("研究计划.md")
    assert controller.runs[0][1] == "请总结"
