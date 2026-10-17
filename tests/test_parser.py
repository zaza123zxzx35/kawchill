from pathlib import Path

from src.connectors.kruphuchuay import parse_area_detail, parse_subject_report
from src.labels import EXAM_GENERAL
from src.parsing import competition_text, parse_number, parse_thai_datetime

FIXTURE = Path(__file__).parent / "fixtures" / "kruphuchuay_en.html"
DETAIL = Path(__file__).parent / "fixtures" / "kruphuchuay_detail.html"


def test_real_english_report_parses_without_inventing_applicants():
    parsed = parse_subject_report(FIXTURE.read_text(encoding="utf-8"))
    assert parsed.structure_ok
    assert parsed.exam_type == EXAM_GENERAL
    assert parsed.reference_at.startswith("2026-10-16T19:50")
    areas = {row["area_key"] for row in parsed.records}
    assert len(areas) == 63
    current = [row for row in parsed.records if row["exam_year"] == 2569 and row["opened"]]
    assert sum(row["openings"] for row in current) == 102
    assert all(row["applicants_known"] is False for row in current)
    assert {row["org_level"] for row in current} >= {"สพป.", "สพม."}
    assert sum(row["openings"] for row in current if row["org_level"] == "สพป.") == 77
    assert sum(row["openings"] for row in current if row["org_level"] == "สพม.") == 25
    closed_2568 = [row for row in parsed.records if row["exam_year"] == 2568]
    assert len(closed_2568) == 63
    assert all(row["opened"] is False and row["openings"] is None for row in closed_2568)
    history = [row for row in parsed.records if row["exam_year"] == 2567 and row["opened"]]
    assert len(history) == 33

    sample = next(row for row in parsed.records if row["area_name"] == "สพป.สุราษฎร์ธานี เขต 2" and row["exam_year"] == 2567)
    assert sample["openings"] == 13
    assert sample["applicants"] == 232
    assert sample["listed_count"] == 82
    assert sample["appointed_count"] == 52
    assert sample["remaining_count"] == 30
    assert competition_text(sample["applicants"], sample["openings"], True) == "1 : 17.8"

    by_year = {item["exam_year"]: item for item in parsed.summaries}
    assert by_year[2567]["openings"] == 413
    assert by_year[2567]["applicants"] == 13421
    assert by_year[2567]["listed_count"] == 4008
    assert by_year[2567]["appointed_count"] == 2130
    assert by_year[2567]["remaining_count"] == 1878
    assert by_year[2568]["openings"] == 124
    assert by_year[2568]["applicants"] == 12022
    assert by_year[2568]["listed_count"] == 6731
    assert by_year[2568]["appointed_count"] == 316
    assert by_year[2568]["remaining_count"] == 6415
    assert by_year[2569]["openings"] == 102
    assert by_year[2569]["applicants_known"] is False
    assert parsed.pending_areas == 120


def test_real_area_detail_staffing_is_separate_from_openings():
    detail = parse_area_detail(DETAIL.read_text(encoding="utf-8"))
    assert detail["structure_ok"]
    assert detail["actual_count"] == 219
    assert detail["standard_count"] == 224
    assert detail["gap_count"] == -5
    assert detail["openings"] == 6
    assert detail["website"] == "https://www.surat2.go.th/"
    assert detail["reference_at"] == "2026-09-26"


def test_changed_html_bad_numbers_and_duplicates():
    html = """
    <html><head><title>ภาษาอังกฤษ</title></head><body>
    การคัดเลือกบุคคลเพื่อบรรจุและแต่งตั้ง ปี พ.ศ. 2569
    <table><tbody>
      <tr>
        <td class="c-name"><div class="n">สพป.ทดสอบ เขต 1</div></td>
        <td data-label="เปิดรับ 2569">2</td>
        <td data-label="สมัคร 2569">-</td>
        <td data-label="สมัคร 2567">abc</td>
        <td data-label="ขึ้นบัญชี 2567">10</td>
        <td data-label="บรรจุ 2567">12</td>
      </tr>
      <tr>
        <td class="c-name"><div class="n">สพป.ทดสอบ เขต 1</div></td>
        <td data-label="เปิดรับ 2569">2</td>
        <td data-label="สมัคร 2569">-</td>
      </tr>
    </tbody></table>
    </body></html>
    """
    parsed = parse_subject_report(html)
    assert parsed.structure_ok
    assert any("ตัวเลขไม่ถูกต้อง" in warning for warning in parsed.warnings)
    assert any("ข้อมูลซ้ำ" in warning for warning in parsed.warnings)
    assert any("เรียกบรรจุมากกว่า" in warning for warning in parsed.warnings)
    year = next(row for row in parsed.records if row["exam_year"] == 2567)
    assert year["applicants"] is None
    assert year["remaining_count"] is None
    assert len([row for row in parsed.records if row["exam_year"] == 2569]) == 1


def test_empty_html_does_not_crash():
    parsed = parse_subject_report("<html><body>เปลี่ยนโครง</body></html>")
    assert parsed.structure_ok is False
    assert parsed.records == []


def test_number_and_date_helpers():
    assert parse_number("-") == (None, False, None)
    assert parse_number("") == (None, False, None)
    assert parse_number("1,234")[0] == 1234
    assert parse_thai_datetime("ข้อมูล ณ วันที่ 16 ตุลาคม 2569 เวลา 19:50 น.").startswith("2026-10-16T19:50")
    assert competition_text(None, 2, False) == "ข้อมูลไม่เพียงพอ"
