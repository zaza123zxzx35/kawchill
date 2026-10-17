from src.connectors.gazette import (
    classify_exam_type,
    document_matches,
    exam_year_from_text,
    extraction_status,
    parse_english_account_table,
    parse_notice_listing,
)
from src.connectors.notices import interpret_notice_text
from src.db import connect, init_db
from src.labels import EXAM_EXISTING_LIST, EXAM_GENERAL, EXAM_NEWS, EXAM_SPECIAL
from src.service import _save_source_notice


TABLE = """
คณิตศาสตร์
ภาษาไทย
ภาษาอังกฤษ
1
2
3
1 สพป.กระบี่ ขึ้นบัญชี 82 80 97
1 สพป.กระบี่ เรียกบรรจุ 43 76 29
1 สพป.กระบี่ คงเหลือ 39 4 68
2 สพป.ทดสอบ เขต 9 ขึ้นบัญชี 1 1 5
2 สพป.ทดสอบ เขต 9 เรียกบรรจุ 1 1 9
2 สพป.ทดสอบ เขต 9 คงเหลือ 0 0 0
"""

SPACED = """
ภาษาอ ังกฤษ
1
1 สพป. กระบี่ ขึ้นบัญชี 4
1 สพป. กระบี่ เรียกบรรจุ 1
1 สพป. กระบี่ คงเหลือ 3
"""


def test_listing_keeps_assistant_teacher_links_only():
    html = """
    <a href="/archives/66540">การสอบแข่งขันตำแหน่งครูผู้ช่วย ปี พ.ศ. 2569</a>
    <a href="/archives/1">จัดซื้อครุภัณฑ์</a>
    <a href="/files/บัญชีคงเหลือ-ครูผู้ช่วย-ปี-67.pdf">บัญชีคงเหลือครูผู้ช่วย</a>
    """
    found = parse_notice_listing(html, "https://personnel.obec.go.th/web/archives/category/eoffice")
    assert len(found) == 2
    assert found[0]["exam_type"] == EXAM_GENERAL
    assert found[0]["exam_year"] == 2569
    assert found[1]["exam_type"] == EXAM_EXISTING_LIST
    assert found[1]["exam_year"] == 2567


def test_exam_types_stay_separate():
    assert classify_exam_type("ครูผู้ช่วย กรณีพิเศษ ปี 2569") == EXAM_SPECIAL
    assert classify_exam_type("ใช้บัญชีผู้สอบแข่งขันได้ของเขตอื่น") == "other_account"
    assert classify_exam_type("ปฐมนิเทศครูผู้ช่วย") == EXAM_NEWS
    assert classify_exam_type("รับสมัครครูผู้ช่วย รอบทั่วไป") == EXAM_GENERAL


def test_account_table_uses_only_the_english_column():
    rows = {row["area_name"]: row for row in parse_english_account_table(TABLE)}
    assert rows["สพป.กระบี่"]["listed"] == 97
    assert rows["สพป.กระบี่"]["appointed"] == 29
    assert rows["สพป.กระบี่"]["remaining"] == 68
    assert rows["สพป.กระบี่"]["consistent"] is True
    assert rows["สพป.ทดสอบเขต9"]["consistent"] is False
    spaced = parse_english_account_table(SPACED)
    assert spaced[0]["area_name"] == "สพป.กระบี่"
    assert spaced[0]["appointed"] == 1


def test_unreadable_pdf_does_not_invent_numbers():
    result = interpret_notice_text("%%% no thai text %%%")
    assert result["applicants"] is None
    assert result["openings"] is None
    assert result["appointed_count"] is None
    assert result["call_rank_reached"] is None
    assert extraction_status("f i Q garbled") == "review"
    assert parse_english_account_table("f i Q garbled") == []


def test_document_search_matches_subject_district_key_and_keyword():
    english = {
        "subject": "ภาษาอังกฤษ",
        "area_name": "สพป.ชลบุรี เขต 3",
        "area_key": "2/104",
        "title": "บัญชีคงเหลือครูผู้ช่วย",
        "exam_type": "existing_list",
        "message": "บันทึก 1 เขต",
        "url": "https://personnel.obec.go.th/account.pdf",
    }
    special = {"subject": "ภาษาอังกฤษ", "title": "ครูผู้ช่วย กรณีพิเศษ", "exam_type": "special", "message": "", "url": "https://example.test/special"}
    assert document_matches(english, "ภาษาอังกฤษ")
    assert document_matches(english, "สพป.ชลบุรี เขต 3")
    assert document_matches(english, "2/104")
    assert document_matches(english, "บัญชีคงเหลือ")
    assert document_matches(special, "กรณีพิเศษ")
    assert document_matches(special, "สพป.ชลบุรี เขต 3") is False


def test_reference_date_does_not_replace_account_year():
    assert exam_year_from_text("บัญชีคงเหลือ-ครูผู้ช่วย-ปี-68-ข้อมูล-ณ-วันที่-30-กันยายน-2569.pdf") == 2568
    assert exam_year_from_text("การสอบแข่งขันตำแหน่งครูผู้ช่วย ปี พ.ศ. 2569") == 2569


def test_uneven_account_row_is_not_saved_as_english():
    text = """
    คณิตศาสตร์
    ภาษาไทย
    ภาษาอังกฤษ
    1
    1 สพป.กระบี่ ขึ้นบัญชี 82 80
    1 สพป.กระบี่ เรียกบรรจุ 43 76
    1 สพป.กระบี่ คงเหลือ 39 4
    """
    assert parse_english_account_table(text) == []


def test_same_document_url_is_not_duplicated(tmp_path):
    db = tmp_path / "radar.db"
    init_db(db)
    notice = {"url": "https://example.test/a", "title": "ครูผู้ช่วย", "exam_type": EXAM_NEWS, "exam_year": 2569, "doc_kind": "article", "announced_at": None}
    assert _save_source_notice(db, "prd_teacher", notice, "listed", "ข่าว") is True
    assert _save_source_notice(db, "prd_teacher", notice, "listed", "ข่าว") is False
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM source_documents").fetchone()["n"] == 1
