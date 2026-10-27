from io import BytesIO

import pytest

openpyxl = pytest.importorskip("openpyxl")

from backend.meeting_export import filter_meetings, render_meeting_workbook


def event(event_id="EV-1", **changes):
    value = {
        "id": event_id,
        "title": "普京—威特科夫会晤",
        "track": "ru-us",
        "track_label": "俄美外交",
        "topics": ["ukraine"],
        "family": "meeting",
        "date_start": "2026-10-26",
        "date_end": "2026-10-27",
        "precision": "range",
        "date_raw": "10月26日至27日",
        "location": "莫斯科",
        "persons": [{"id": "putin", "name": "普京", "role": "俄罗斯总统", "country": "俄罗斯"}],
        "status": "confirmed",
        "status_label": "已确认",
        "inactive": False,
        "conflict": False,
        "non_meeting": False,
        "first_seen": "2026-10-20T08:00:00+08:00",
        "last_seen": "2026-10-26T08:00:00+08:00",
        "baseline_note": "正式通报",
        "sources": [
            {"name": "克里姆林宫", "url": "https://example.org/meeting"},
            {"name": "示例媒体", "url": "https://example.org/report"},
        ],
    }
    value.update(changes)
    return value


def test_filter_meetings_matches_topic_person_status_date_and_inactive():
    snapshot = {
        "created_at": "2026-10-26T06:00:00Z",
        "meetings": [
            event(),
            event("EV-2", status="canceled", status_label="已取消", inactive=True),
            event("EV-3", topics=["usiran"], date_start="2026-11-03", date_end=""),
        ],
    }

    rows, start, end = filter_meetings(
        snapshot, topic="ukraine", status="confirmed", person="普京",
        start="2026-10-27", end="2026-10-27", include_inactive=False,
    )

    assert [item["id"] for item in rows] == ["EV-1"]
    assert (start, end) == ("2026-10-27", "2026-10-27")


def test_render_meeting_workbook_has_professional_structure_and_safe_cells():
    rows = [event(title="=HYPERLINK(\"bad\")")]
    content = render_meeting_workbook(rows, {
        "exported_at": "2026-10-26T12:00:00+00:00",
        "as_of": "2026-10-26T06:00:00+00:00",
        "topic": "俄乌相关日程",
        "status": "已确认",
        "person": "普京",
        "start": "2026-10-26",
        "end": "2026-10-27",
        "include_inactive": False,
        "basis": "冻结快照中的结构化会晤日历。",
    })
    workbook = openpyxl.load_workbook(BytesIO(content), data_only=False)

    assert workbook.sheetnames == ["会晤台账", "导出说明"]
    sheet = workbook["会晤台账"]
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:R2"
    assert [cell.value for cell in sheet[1]][:5] == ["事件", "相关人物（职务）", "国家/地区", "会晤地点", "开始日期"]
    assert sheet["A2"].value.startswith("'")
    assert sheet["B2"].value == "普京（俄罗斯总统）"
    assert sheet["E2"].number_format == "yyyy-mm-dd"
    assert sheet["O2"].hyperlink.target == "https://example.org/meeting"
    assert "https://example.org/report" in sheet["O2"].value
    assert workbook["导出说明"]["B9"].value == 1
    assert not any("snapshot" in str(cell.value).lower() for row in workbook["导出说明"] for cell in row if cell.value)
    assert not any(cell.data_type == "f" for worksheet in workbook for row in worksheet.iter_rows() for cell in row)

