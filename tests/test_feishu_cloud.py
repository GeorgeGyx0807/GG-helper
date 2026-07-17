from datetime import datetime, timezone
from pathlib import Path

import pytest

from poppy.integrations.feishu_cloud import (
    CloudReference,
    FeishuCloudError,
    FeishuCloudReader,
    FeishuOpenAPIError,
)


class FakeAPI:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params or {}))
        response = self.responses.get(path)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise AssertionError(f"unexpected API call: {path}")
        return response


def test_parse_supported_cloud_links_and_calendar_command():
    text = (
        "读取飞书日历 读 https://tenant.feishu.cn/docx/doc_1 ，"
        "https://tenant.feishu.cn/wiki/wiki_2 "
        "https://tenant.feishu.cn/base/app_3?table=tbl_4&view=vew_5"
    )

    references = FeishuCloudReader.parse_references(text)

    assert [(item.kind, item.token) for item in references[:3]] == [
        ("docx", "doc_1"),
        ("wiki", "wiki_2"),
        ("bitable", "app_3"),
    ]
    assert references[2].table_id == "tbl_4"
    assert references[2].view_id == "vew_5"
    assert references[3].kind == "calendar"


def test_docx_snapshot_is_session_scoped_and_contains_source(tmp_path):
    api = FakeAPI(
        {
            "docx/v1/documents/doc_1": {"document": {"title": "研究计划"}},
            "docx/v1/documents/doc_1/raw_content": {"content": "背景\n方法\n结论"},
        }
    )
    reader = FeishuCloudReader("cli_test", "secret", tmp_path, api=api)
    url = "https://tenant.feishu.cn/docx/doc_1"

    snapshot = reader.read_message(f"{url} 请总结", "session_a")[0]

    path = Path(snapshot.path)
    assert path.is_relative_to(tmp_path / "cloud" / "session_a")
    assert snapshot.title == "研究计划"
    content = path.read_text(encoding="utf-8")
    assert url in content
    assert "背景\n方法\n结论" in content


def test_wiki_node_resolves_real_docx_token(tmp_path):
    api = FakeAPI(
        {
            "wiki/v2/spaces/get_node": {
                "node": {
                    "title": "知识库文章",
                    "obj_type": "docx",
                    "obj_token": "real_doc",
                }
            },
            "docx/v1/documents/real_doc": {"document": {"title": "ignored"}},
            "docx/v1/documents/real_doc/raw_content": {"content": "知识库正文"},
        }
    )
    reader = FeishuCloudReader("cli_test", "secret", tmp_path, api=api)

    snapshot = reader.read_message(
        "https://tenant.feishu.cn/wiki/wiki_token",
        "session_wiki",
    )[0]

    assert snapshot.title == "知识库文章"
    assert "知识库正文" in Path(snapshot.path).read_text(encoding="utf-8")
    assert api.calls[0] == ("wiki/v2/spaces/get_node", {"token": "wiki_token"})


def test_bitable_records_are_rendered_as_searchable_markdown(tmp_path):
    api = FakeAPI(
        {
            "bitable/v1/apps/app_1": {"app": {"name": "项目台账"}},
            "bitable/v1/apps/app_1/tables/tbl_1/records": {
                "items": [
                    {
                        "record_id": "rec_1",
                        "fields": {"负责人": [{"name": "George"}], "状态": "进行中"},
                    }
                ],
                "has_more": False,
            },
        }
    )
    reader = FeishuCloudReader("cli_test", "secret", tmp_path, api=api)

    snapshot = reader.read_message(
        "https://tenant.feishu.cn/base/app_1?table=tbl_1",
        "session_base",
    )[0]

    content = Path(snapshot.path).read_text(encoding="utf-8")
    assert "项目台账" in content
    assert "负责人" in content
    assert "George" in content
    assert "状态: 进行中" in content


def test_calendar_command_reads_application_visible_events(tmp_path):
    api = FakeAPI(
        {
            "calendar/v4/calendars": {
                "calendar_list": [
                    {"calendar_id": "calendar_1", "summary": "团队日历", "is_deleted": False}
                ]
            },
            "calendar/v4/calendars/calendar_1/events": {
                "items": [
                    {
                        "summary": "论文讨论",
                        "start_time": {"timestamp": "1784160000"},
                        "end_time": {"timestamp": "1784163600"},
                        "description": "讨论实验结果",
                    }
                ]
            },
        }
    )
    reader = FeishuCloudReader(
        "cli_test",
        "secret",
        tmp_path,
        api=api,
        now=lambda: datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    snapshot = reader.read_message("读取飞书日历 2026-07-16 2026-07-20", "session_cal")[0]

    content = Path(snapshot.path).read_text(encoding="utf-8")
    assert "团队日历" in content
    assert "论文讨论" in content
    assert "讨论实验结果" in content
    event_call = api.calls[-1]
    assert event_call[1]["start_time"] == 1784160000
    assert event_call[1]["end_time"] == 1784592000


def test_permission_failure_becomes_actionable_user_message(tmp_path):
    api = FakeAPI(
        {
            "docx/v1/documents/doc_denied": FeishuOpenAPIError(
                "forbidden",
                code=99991672,
                status=403,
            )
        }
    )
    reader = FeishuCloudReader("cli_test", "secret", tmp_path, api=api)

    with pytest.raises(FeishuCloudError) as error:
        reader.read_message("https://tenant.feishu.cn/docx/doc_denied", "session_denied")

    assert "开通“查看新版文档”权限" in str(error.value)
    assert "共享给 Poppy 应用" in str(error.value)


def test_unsupported_wiki_object_is_explained(tmp_path):
    api = FakeAPI(
        {
            "wiki/v2/spaces/get_node": {
                "node": {"title": "表格", "obj_type": "sheet", "obj_token": "sheet_1"}
            }
        }
    )
    reader = FeishuCloudReader("cli_test", "secret", tmp_path, api=api)

    with pytest.raises(FeishuCloudError) as error:
        reader._read_reference(CloudReference("wiki", token="wiki_1"))

    assert "当前直接读取支持新版文档和多维表格" in str(error.value)
