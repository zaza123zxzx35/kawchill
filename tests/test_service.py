import json
from datetime import datetime, timedelta, timezone

from src.connectors.importer import parse_import_file
from src.db import connect, get_pref, init_db, set_pref
from src.labels import STATUS_FAIL, STATUS_FRESH, STATUS_STALE
from src.labels import EXAM_EXISTING_LIST, EXAM_GENERAL, SUBJECT
from src.service import _save_account_event, _store_found_applicants, classify_data_status, import_records, load_dashboard, refresh_all


REPORT = """
<html><head><title>ภาษาอังกฤษ</title></head><body>
การคัดเลือกบุคคลเพื่อบรรจุและแต่งตั้งเข้ารับราชการเป็นข้าราชการครู ปี พ.ศ. 2569
ข้อมูล ณ วันที่ 16 ตุลาคม 2569 เวลา 19:50 น.
<table><tbody>
<tr>
  <td class="c-name"><div class="n">สพป.ทดสอบ เขต 1</div></td>
  <td data-label="เปิดรับ 2569">2</td>
  <td data-label="สมัคร 2569">-</td>
  <td class="none-yr" data-label="ปี 2568">ไม่เปิดสอบ</td>
  <td data-label="เปิดรับ 2567">4</td>
  <td data-label="สมัคร 2567">40</td>
  <td data-label="ขึ้นบัญชี 2567">10</td>
  <td data-label="บรรจุ 2567">6</td>
  <td><a href="report.php?r=2/181">รายละเอียด</a></td>
</tr>
</tbody></table>
<div aria-label="ตำแหน่งที่เปิดรับ ปี 2567 4, ปี 2569 2"></div>
<div aria-label="ผู้สมัคร ปี 2567 40, ปี 2569 ไม่มีข้อมูล"></div>
เขตรอประกาศ 120 เขต
</body></html>
"""

DETAIL = """
<html><body>
<tr data-search="ภาษาอังกฤษ">
  <td><small class="std-sub">มี 10 / ควรมี 12</small></td>
  <td data-label="เปิดสอบ (ตำแหน่ง)">2</td>
</tr>
<a href="https://area.example.go.th/">เว็บไซต์</a>
ข้อมูล ณ 26/09/2569
</body></html>
"""

RUNTIME = '36:"components/major10-years-report",36:"0123456789abcdef0123"'
SCRIPT = '{id:5,majorTitle:"ภาษาอังกฤษ",stadardMajors:46567,teacherNumber:41444,differentNumber:-5123,sumExit:6474,sumNeed:12323}'


def http_ok(url: str):
    if "report.php?r=4/5" in url:
        return 200, REPORT
    if "report.php?r=2/" in url:
        return 200, DETAIL
    if "runtime" in url:
        return 200, RUNTIME
    if "major10-years-report" in url:
        return 200, SCRIPT
    if "/info/majorStandard/major10Years" in url:
        return 200, '<script src="/_nuxt/runtime.abc123.js"></script>'
    if url.startswith("https://www.obec.go.th") or "personnel" in url or "otepc" in url:
        return 200, "<html><title>หน้าแรก</title><p>ข่าวทั่วไป</p></html>"
    raise AssertionError(url)


def test_refresh_saves_sqlite_and_keeps_success_time_when_site_fails(tmp_path):
    db = tmp_path / "radar.db"
    first = refresh_all(db, http_ok)
    assert any(item["source"] == "kruphuchuay" and item["status"] == "success" for item in first)
    with connect(db) as conn:
        success = conn.execute("SELECT finished_at FROM fetch_runs WHERE source_code='kruphuchuay' AND status='success'").fetchone()["finished_at"]
        count = conn.execute("SELECT COUNT(*) AS n FROM recruitment_records").fetchone()["n"]
        children = conn.execute("SELECT COUNT(*) AS n FROM account_call_history").fetchone()["n"]
        staffing = conn.execute("SELECT actual_count, standard_count FROM staffing_records WHERE scope='area'").fetchone()
        national = conn.execute("SELECT gap_count FROM staffing_records WHERE source_code='hrms_obec'").fetchone()
        website = conn.execute("SELECT area_website FROM recruitment_records WHERE exam_year=2569").fetchone()["area_website"]
    assert count == 3
    assert children == 3
    assert staffing["actual_count"] == 10
    assert staffing["standard_count"] == 12
    assert national["gap_count"] == -5123
    assert website == "https://area.example.go.th/"

    again = refresh_all(db, http_ok)
    assert any(item["status"] == "success" for item in again)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM recruitment_records").fetchone()["n"] == 3
        success = conn.execute(
            "SELECT finished_at FROM fetch_runs WHERE source_code='kruphuchuay' AND status='success' ORDER BY id DESC"
        ).fetchone()["finished_at"]

    def broken(url):
        raise TimeoutError("เว็บไซต์ไม่ตอบสนอง")

    failed = refresh_all(db, broken)
    assert any(item["source"] == "kruphuchuay" and item["status"] == "failed" for item in failed)
    with connect(db) as conn:
        still = conn.execute("SELECT finished_at FROM fetch_runs WHERE source_code='kruphuchuay' AND status='success' ORDER BY id DESC").fetchone()["finished_at"]
        latest = conn.execute("SELECT status FROM fetch_runs WHERE source_code='kruphuchuay' ORDER BY id DESC").fetchone()["status"]
        assert conn.execute("SELECT COUNT(*) AS n FROM recruitment_records").fetchone()["n"] == count
    assert still == success
    assert latest == "failed"
    view = load_dashboard(db)
    assert view["status"] == STATUS_FAIL
    assert view["last_success"] == success
    assert len(view["areas"]) == 1


def test_changed_html_does_not_wipe_saved_rows(tmp_path):
    db = tmp_path / "radar.db"
    refresh_all(db, http_ok)

    def changed(url):
        if "report.php?r=4/5" in url:
            return 200, "<html><body>โครงใหม่</body></html>"
        return http_ok(url)

    result = refresh_all(db, changed)
    assert any(item["source"] == "kruphuchuay" and item["status"] == "structure_changed" for item in result)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM recruitment_records WHERE openings=2").fetchone()["n"] == 1


def test_preferences_and_csv_import_roundtrip(tmp_path):
    db = tmp_path / "radar.db"
    init_db(db)
    with connect(db) as conn:
        set_pref(conn, "refresh_hours", "6")
        set_pref(conn, "favorite_area_keys", json.dumps(["2/181"]))
        assert get_pref(conn, "refresh_hours") == "6"
    payload = "ปีสอบ,ประเภทการสอบ,วิชาเอก,เขตพื้นที่,เปิดรับ,ผู้สมัคร,ขึ้นบัญชี,เรียกบรรจุ\n2568,การคัดเลือกทั่วไป,ภาษาอังกฤษ,สพม.ทดสอบ,1,8,5,5\n".encode("utf-8")
    records, warnings = parse_import_file("manual.csv", payload)
    assert warnings == []
    outcome = import_records(db, records)
    assert outcome["records"] == 1
    with connect(db) as conn:
        row = conn.execute("SELECT applicants, remaining_count, source_code FROM recruitment_records WHERE source_code='csv_import'").fetchone()
    assert row["applicants"] == 8
    assert row["remaining_count"] == 0
    assert row["source_code"] == "csv_import"


WAIT_PAGE = """
<div class='updated'>ข้อมูล ณ วันที่ 16 ตุลาคม 2569 เวลา 19:50 น.</div>
<div class='esa-card'><div class='n'>สำนักงานเขตพื้นที่การศึกษาประถมศึกษาเชียงราย เขต 4</div></div>
"""
CLOSED_PAGE = "<div class='esa-card'><div class='n'>สำนักงานเขตพื้นที่การศึกษาประถมศึกษาอุบลราชธานี เขต 1</div></div>"
INDEX_PAGE = """
<table><tbody>
<tr><td class='c-name'><div class='n'>สพป.ทดสอบ เขต 1</div></td></tr>
<tr><td class='c-name'><div class='n'>สพป.เชียงราย เขต 4</div></td></tr>
<tr><td class='c-name'><div class='n'>สพป.อุบลราชธานี เขต 1</div></td></tr>
<tr><td class='c-name'><div class='n'>สพป.กรุงเทพมหานคร</div></td></tr>
</tbody></table>
"""


def http_with_lists(url: str):
    if "report.php?r=6" in url:
        return 200, WAIT_PAGE
    if "report.php?r=5" in url:
        return 200, CLOSED_PAGE
    if "report.php?r=1" in url:
        return 200, INDEX_PAGE
    return http_ok(url)


def test_coverage_refresh_does_not_duplicate_or_wipe_waiting_rows(tmp_path):
    db = tmp_path / "radar.db"
    refresh_all(db, http_with_lists)
    refresh_all(db, http_with_lists)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM coverage_records").fetchone()["n"] == 4
        assert conn.execute("SELECT status FROM coverage_records WHERE area_name='สพป.เชียงราย เขต 4'").fetchone()["status"] == "รอประกาศ"
        assert conn.execute("SELECT call_rank_reached, announced_at FROM account_call_history WHERE exam_year=2567").fetchone()["call_rank_reached"] is None

    def broken(url: str):
        if "report.php?r=6" in url:
            raise TimeoutError("รายชื่อรอประกาศไม่ตอบ")
        return http_with_lists(url)

    refresh_all(db, broken)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM coverage_records WHERE status='รอประกาศ'").fetchone()["n"] == 1
        latest = conn.execute("SELECT status FROM fetch_runs WHERE source_code='kruphuchuay_coverage' ORDER BY id DESC LIMIT 1").fetchone()
        assert latest["status"] == "failed"


def test_found_applicants_do_not_duplicate_or_overwrite_a_conflict(tmp_path):
    db = tmp_path / "radar.db"
    refresh_all(db, http_ok)
    with connect(db) as conn:
        area = conn.execute("SELECT area_key, area_name FROM recruitment_records WHERE exam_year=2569").fetchone()
    saved = _store_found_applicants(db, dict(area), 18, "https://area.example.go.th/notice")
    again = _store_found_applicants(db, dict(area), 18, "https://area.example.go.th/notice")
    conflict = _store_found_applicants(db, dict(area), 40, "https://area.example.go.th/other")
    with connect(db) as conn:
        row = conn.execute("SELECT applicants, applicants_known FROM recruitment_records WHERE exam_year=2569").fetchone()
        changes = conn.execute("SELECT COUNT(*) AS n FROM record_changes WHERE field_name='applicants'").fetchone()["n"]
        warnings = conn.execute(
            "SELECT COUNT(*) AS n FROM data_warnings WHERE message LIKE '%จำนวนผู้สมัคร%'"
        ).fetchone()["n"]
        rows = conn.execute("SELECT COUNT(*) AS n FROM recruitment_records WHERE exam_year=2569").fetchone()["n"]
    assert saved is True
    assert again is False
    assert conflict is False
    assert row["applicants"] == 18
    assert row["applicants_known"] == 1
    assert changes == 1
    assert warnings == 1
    assert rows == 1


def test_account_balance_does_not_overwrite_the_general_exam(tmp_path):
    db = tmp_path / "radar.db"
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO recruitment_records (
                source_code, exam_year, exam_type, subject, affiliation, org_level, area_name, area_key,
                openings, applicants_known, appointed_count, opened, observed_at
            ) VALUES ('kruphuchuay', 2567, ?, ?, 'สพฐ.', 'สพป.', 'สพป.ชลบุรี เขต 3', '2/104', 1, 1, 63, 1, '2026-10-17T00:00:00+07:00')
            """,
            (EXAM_GENERAL, SUBJECT),
        )
    row = {"area_name": "สพป.ชลบุรีเขต3", "appointed": 73, "remaining": 44, "snippet": "เรียกบรรจุ 73 คงเหลือ 44"}
    _save_account_event(db, "personnel_eoffice", "2/104", "สพป.ชลบุรี เขต 3", 2567, "https://example.test/account.pdf", row)
    _save_account_event(db, "personnel_eoffice", "2/104", "สพป.ชลบุรี เขต 3", 2567, "https://example.test/account.pdf", row)
    with connect(db) as conn:
        exam = conn.execute("SELECT appointed_count FROM recruitment_records WHERE exam_type = ?", (EXAM_GENERAL,)).fetchone()
        account = conn.execute("SELECT COUNT(*) AS n, exam_type FROM call_events").fetchone()
        warnings = conn.execute("SELECT COUNT(*) AS n FROM data_warnings").fetchone()["n"]
    assert exam["appointed_count"] == 63
    assert account["n"] == 1
    assert account["exam_type"] == EXAM_EXISTING_LIST
    assert warnings == 1


def test_status_uses_success_time_not_current_time():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone(timedelta(hours=7)))
    fresh = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(hours=8)).isoformat()
    assert classify_data_status(fresh, "success", 6, now) == STATUS_FRESH
    assert classify_data_status(stale, "success", 6, now) == STATUS_STALE
    assert classify_data_status(fresh, "failed", 6, now) == STATUS_FAIL
    assert classify_data_status(None, "failed", 6, now) == STATUS_FAIL
