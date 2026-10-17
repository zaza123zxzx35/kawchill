from src.connectors.kruphuchuay import parse_area_name_list, shorten_area_name
from src.coverage import build_coverage
from src.labels import COVERAGE_ANNOUNCED, COVERAGE_NONE, COVERAGE_SOURCE_CLOSED, COVERAGE_WAITING


def test_waiting_names_are_not_treated_as_closed():
    english = [
        {
            "exam_year": 2569,
            "opened": True,
            "openings": 6,
            "area_name": "สพป.สุราษฎร์ธานี เขต 2",
            "area_key": "2/181",
            "source_url": "https://www.kruphuchuay.com/report.php?r=4/5",
        }
    ]
    rows, warnings = build_coverage(
        english,
        ["สพป.เชียงราย เขต 4"],
        ["สพป.อุบลราชธานี เขต 1"],
        ["สพป.สุราษฎร์ธานี เขต 2", "สพป.เชียงราย เขต 4", "สพป.อุบลราชธานี เขต 1", "สพป.กรุงเทพมหานคร"],
        reference_at="2026-10-16T19:50+07:00",
        subject_url="https://www.kruphuchuay.com/report.php?r=4/5",
        waiting_url="https://www.kruphuchuay.com/report.php?r=6",
        closed_url="https://www.kruphuchuay.com/report.php?r=5",
        index_url="https://www.kruphuchuay.com/report.php?r=1",
    )
    by_name = {row["area_name"]: row for row in rows}
    assert by_name["สพป.สุราษฎร์ธานี เขต 2"]["status"] == COVERAGE_ANNOUNCED
    assert by_name["สพป.สุราษฎร์ธานี เขต 2"]["announced_at"] is None
    assert by_name["สพป.เชียงราย เขต 4"]["status"] == COVERAGE_WAITING
    assert "สรุปไม่ได้ว่าไม่เปิดสอบ" in by_name["สพป.เชียงราย เขต 4"]["evidence"]
    assert by_name["สพป.อุบลราชธานี เขต 1"]["status"] == COVERAGE_SOURCE_CLOSED
    assert by_name["สพป.กรุงเทพมหานคร"]["status"] == COVERAGE_NONE
    assert warnings == []


def test_incomplete_lists_do_not_invent_missing_english_rows():
    rows, warnings = build_coverage(
        [],
        None,
        ["สพป.อุบลราชธานี เขต 1"],
        ["สพป.กรุงเทพมหานคร"],
        reference_at=None,
        subject_url="https://example.test/subject",
        waiting_url="https://example.test/wait",
        closed_url="https://example.test/closed",
        index_url="https://example.test/index",
    )
    assert [row["area_name"] for row in rows] == ["สพป.อุบลราชธานี เขต 1"]
    assert any("รอประกาศ" in warning for warning in warnings)
    assert all(row["status"] != COVERAGE_NONE for row in rows)


def test_short_area_names_and_card_list():
    assert shorten_area_name("สำนักงานเขตพื้นที่การศึกษาประถมศึกษาเชียงราย เขต 4") == "สพป.เชียงราย เขต 4"
    html = """
    <div class='updated'>ข้อมูล ณ วันที่ 16 ตุลาคม 2569 เวลา 19:50 น.</div>
    <div class='esa-card'><div class='n'>สำนักงานเขตพื้นที่การศึกษาประถมศึกษาเชียงราย เขต 4</div></div>
    """
    names, reference, found = parse_area_name_list(html)
    assert found is True
    assert names == ["สพป.เชียงราย เขต 4"]
    assert reference.startswith("2026-10-16T19:50")


def test_missing_list_markup_is_unverified():
    names, _reference, found = parse_area_name_list("<html><body>ไม่มีตาราง</body></html>")
    assert names == []
    assert found is False
