from src.connectors.gazette import (
    _apply_account_page,
    _english_x_from_chars,
    _read_account_row,
    account_rows_from_grouped,
    match_area_name,
    norm_area,
)
from src.guide_rules import apply_choice_filters, evidence_review, explain_area, guide_watch
from src.scoring import AreaInput, YearStat, score_areas


def _area(name: str, key: str, openings=None, listed=None, appointed=None, org="สพป.") -> AreaInput:
    years = {}
    if openings is not None:
        years[2569] = YearStat(2569, True, openings, None, False, None, None)
    if listed is not None:
        years[2567] = YearStat(2567, True, 1, None, False, listed, appointed)
    return AreaInput(key, name, org, years, source_url="https://www.kruphuchuay.com/report.php?r=4/5")


def test_blank_english_cell_stays_missing_and_bad_arithmetic_is_rejected():
    grouped = {}
    _read_account_row([(20, "1 สพป.ทดสอบ เขต 2"), (70, "ขึ้นบัญชี"), (210, "9")], 144, grouped)
    _read_account_row([(20, "1 สพป.ทดสอบ เขต 2"), (70, "เรียกบรรจุ 6"), (144, "4")], 144, grouped)
    _read_account_row([(20, "1 สพป.ทดสอบ เขต 2"), (70, "คงเหลือ"), (144, "1")], 144, grouped)
    row = account_rows_from_grouped(grouped)[0]
    assert "listed" not in grouped["สพป.ทดสอบเขต2"] or grouped["สพป.ทดสอบเขต2"].get("listed") is None
    assert row["appointed"] == 4
    assert row["remaining"] == 1
    assert row["consistent"] is False


def test_status_rows_without_a_name_stay_with_the_district_above():
    grouped = {}
    _apply_account_page(
        grouped,
        144,
        {
            30: [(20, "26 สพป.เชียงใหม่ เขต 3"), (88, "ขึ้นบัญชี"), (148, "321")],
            20: [(88, "เรียกบรรจุ"), (150, "15")],
            10: [(88, "คงเหลือ"), (146, "306")],
        },
    )
    row = account_rows_from_grouped(grouped)[0]
    assert row["area_name"] == "สพป.เชียงใหม่เขต3"
    assert row["listed"] == 321
    assert row["appointed"] == 15
    assert row["remaining"] == 306
    assert row["consistent"] is True


def test_vertical_header_finds_english_without_inventing_a_column():
    chars = []
    for y, char in enumerate("ภาษาอังกฤษ"):
        chars.append((144.2, 476 + y, char))
    chars.append((130.0, 476, "ภ"))
    assert _english_x_from_chars(chars) == 144
    assert _english_x_from_chars([(200.0, 100, "1")]) is None


def test_font_key_matches_one_district_only():
    areas = {
        norm_area("สพป.กำแพงเพชร เขต 2"): ("k2", "สพป.กำแพงเพชร เขต 2"),
        norm_area("สพป.กำแพงเพชร เขต 1"): ("k1", "สพป.กำแพงเพชร เขต 1"),
    }
    assert match_area_name(areas, "สพป.กาแพงเพชรเขต2") == ("k2", "สพป.กำแพงเพชร เขต 2")
    assert match_area_name(areas, "สพป.กาแพงเพชรเขต") is None


def test_guide_does_not_lock_a_district_or_treat_shortage_as_an_opening():
    opened = _area("สพป.สมุทรสาคร", "a", openings=4, listed=100, appointed=90)
    opened.staffing_actual = 10
    opened.staffing_standard = 20
    zone1 = _area("สพป.นครราชสีมา เขต 1", "b", openings=2, listed=50, appointed=40)
    other = _area("สพป.นครราชสีมา เขต 2", "c", openings=1, listed=10, appointed=4)
    secondary = _area("สพม.ขอนแก่น", "d", openings=3, listed=10, appointed=9, org="สพม.")
    shortage_only = AreaInput("e", "สพป.ไม่มีประกาศ", "สพป.", {}, staffing_actual=1, staffing_standard=9)
    account = [{
        "area_key": "a",
        "exam_year": 2567,
        "appointed_count": 20,
        "remaining_count": 12,
        "evidence": "listed 32 | appointed 20 | remaining 12",
        "source_url": "file:///balance-67.pdf",
    }]
    result = guide_watch(
        [opened, zone1, other, secondary, shortage_only],
        [opened, zone1, other, secondary, shortage_only],
        2569,
        account,
    )
    assert result["locked"] is False
    assert "โอกาสสอบติด" in result["headline"]
    assert [item["area_name"] for item in result["primary"]] == ["สพป.สมุทรสาคร", "สพป.นครราชสีมา เขต 2"]
    assert [item["area_name"] for item in result["backup"]] == ["สพป.นครราชสีมา เขต 1"]
    assert any(item["area_name"] == "สพม.ขอนแก่น" for item in result["hold"])
    assert all(item["area_name"] != "สพป.ไม่มีประกาศ" for item in result["primary"] + result["backup"] + result["hold"])
    detail = explain_area(opened, 2569, account, set())
    assert detail["confidence"] == "รอจำนวนผู้สมัคร"
    assert "total" not in detail
    account_factor = next(item for item in detail["factors"] if item["factor"] == "บัญชีคงเหลือเอกภาษาอังกฤษ")
    assert "2567" in account_factor["text"]
    assert "ไม่ใช่ตำแหน่งเปิดสอบปี 2569" in account_factor["text"]
    opening_factor = next(item for item in detail["factors"] if item["factor"] == "อัตราว่างที่เปิดสอบ")
    assert "4 ตำแหน่ง" in opening_factor["text"]
    account_only = _area("สพป.พัทลุง เขต 2", "f", openings=2)
    balance = [{
        "area_key": "f",
        "exam_year": 2567,
        "appointed_count": 11,
        "remaining_count": 0,
        "evidence": "listed 11 | appointed 11 | remaining 0",
        "source_url": "file:///balance-67.pdf",
    }]
    from_balance = guide_watch([account_only], [account_only], 2569, balance)
    assert from_balance["primary"][0]["area_name"] == "สพป.พัทลุง เขต 2"
    assert from_balance["primary"][0]["call_ratio"] == 1
    scores = score_areas([opened], 2569)
    assert scores[0].total is not None
    assert not any(component.key == "account" for component in scores[0].components)


def test_call_share_uses_one_source_and_does_not_compare_different_definitions():
    exam = _area("สพป.เลย เขต 1", "exam", openings=3, listed=20, appointed=20)
    balance_only = _area("สพป.สตูล", "balance", openings=1)
    conflicted = _area("สพป.ชลบุรี เขต 3", "chon", openings=1, listed=117, appointed=63)
    areas = [exam, balance_only, conflicted]
    events = [
        {
            "area_key": "balance",
            "area_name": "สพป.สตูล",
            "exam_year": 2566,
            "appointed_count": 6,
            "remaining_count": 3,
            "evidence": "listed 9 | appointed 6 | remaining 3",
            "source_url": "file:///balance-66.pdf",
            "announced_at": None,
            "observed_at": "2026-10-17T14:53:07+07:00",
        },
        {
            "area_key": "chon",
            "area_name": "สพป.ชลบุรี เขต 3",
            "exam_year": 2567,
            "appointed_count": 73,
            "remaining_count": 44,
            "evidence": "listed 117 | appointed 73 | remaining 44",
            "source_url": "https://personnel.obec.go.th/example.pdf",
            "announced_at": None,
            "observed_at": "2026-10-17T13:44:46+07:00",
        },
    ]
    review = evidence_review(areas, areas, 2569, events)
    by_name = {row["area_name"]: row for row in review["rows"]}
    assert by_name["สพป.เลย เขต 1"]["group"] == "incomplete"
    assert by_name["สพป.เลย เขต 1"]["numerator"] == 20
    assert by_name["สพป.เลย เขต 1"]["denominator"] == 20
    assert by_name["สพป.เลย เขต 1"]["basis_definition"] == "หน้ารวบรวม"
    assert by_name["สพป.สตูล"]["group"] == "wait"
    assert by_name["สพป.สตูล"]["basis_definition"] == "บัญชีคงเหลือ"
    assert by_name["สพป.สตูล"]["comparable"] is False
    assert by_name["สพป.ชลบุรี เขต 3"]["group"] == "wait"
    assert by_name["สพป.ชลบุรี เขต 3"]["conflicts"]
    unlabeled = dict(events[1])
    unlabeled["evidence"] = "ขึ้นบัญชี 79 64 117 | เรียกบรรจุ 71 64 73 | คงเหลือ 8 0 44"
    unlabeled["observed_at"] = "2026-10-17T13:44:46+07:00"
    unlabeled_review = evidence_review(areas, areas, 2569, [unlabeled])
    unlabeled_row = next(row for row in unlabeled_review["rows"] if row["area_name"] == "สพป.ชลบุรี เขต 3")
    assert "117" not in unlabeled_row["balance_by_year"][2567]
    assert "เรียกบรรจุ 73" in unlabeled_row["balance_by_year"][2567]
    assert unlabeled_row["balance_announced"] == "ไม่พบวันที่"
    assert "2026-10-17" not in unlabeled_row["balance_announced"]
    assert unlabeled_row["conflicts"]
    assert unlabeled_row["group"] == "wait"
    assert review["sufficient"] == []
    assert "โอกาสสอบติด" in review["ratio_note"]
    unchanged = score_areas(areas, 2569)
    again = score_areas(areas, 2569)
    assert [item.total for item in unchanged] == [item.total for item in again]
    assert [item["area_name"] for item in review["watch"]] == ["สพป.เลย เขต 1"]


def test_broken_arithmetic_and_separate_years_stay_uncombined():
    """ตัวเลขในเทสต์นี้เป็นข้อมูลทดสอบ ไม่ใช่แถวจริงในฐานข้อมูล"""
    broken = _area("สพป.ทดสอบสมการ", "bad", openings=1, listed=10, appointed=4)
    broken.years[2567].remaining_count = 9
    broken.years[2566] = YearStat(2566, True, 1, None, False, 5, 2, 3)
    review = evidence_review([broken], [broken], 2569, [])
    row = review["rows"][0]
    assert row["group"] == "wait"
    assert row["recruitment_by_year"][2567] == "ขึ้นบัญชี 10 เรียกบรรจุ 4 คงเหลือ 9"
    assert any("ไม่เท่ากับ" in note for note in row["conflicts"])
    assert "ปี 2566 หน้ารวบรวม 2/5" in row["share_text"]
    assert "10" not in row["share_text"]
    assert row["applicants"] == "รอข้อมูล"
    assert review["sufficient"] == []


def test_consideration_keeps_only_supported_districts_and_does_not_pad_to_five():
    """ตัวเลขในเทสต์นี้เป็นข้อมูลทดสอบ ไม่ใช่แถวจริงในฐานข้อมูล"""
    single = _area("สพป.แหล่งเดียว", "one", openings=6, listed=82, appointed=52)
    single.years[2567].remaining_count = 30
    double = _area("สพป.สองแหล่ง", "two", openings=2, listed=19, appointed=19)
    double.years[2567].remaining_count = 0
    zone = _area("สพป.นครราชสีมา เขต 1", "z1", openings=1, listed=25, appointed=18)
    zone.years[2567].remaining_count = 7
    other = _area("สพป.นครราชสีมา เขต 2", "z2", openings=3, listed=72, appointed=35)
    other.years[2567].remaining_count = 37
    zero_balance = _area("สพป.ศูนย์", "zero", openings=1, listed=34, appointed=33)
    zero_balance.years[2567].remaining_count = 1
    conflicted = _area("สพป.ขัดแย้ง", "bad", openings=1, listed=117, appointed=63)
    conflicted.years[2567].remaining_count = 54
    agreed = _area("สพป.ปีเดียวกัน", "agree", openings=1, listed=11, appointed=11)
    agreed.years[2567].remaining_count = 0
    areas = [single, double, zone, other, zero_balance, conflicted, agreed]
    events = [
        {
            "area_key": "two",
            "area_name": "สพป.สองแหล่ง",
            "exam_year": 2566,
            "appointed_count": 25,
            "remaining_count": 0,
            "evidence": "listed 25 | appointed 25 | remaining 0",
            "source_url": "file:///balance-66.pdf",
            "announced_at": None,
        },
        {
            "area_key": "zero",
            "area_name": "สพป.ศูนย์",
            "exam_year": 2566,
            "appointed_count": 0,
            "remaining_count": 0,
            "evidence": "listed 0 | appointed 0 | remaining 0",
            "source_url": "file:///balance-66.pdf",
            "announced_at": None,
            "observed_at": "2026-10-17T14:53:07+07:00",
        },
        {
            "area_key": "bad",
            "area_name": "สพป.ขัดแย้ง",
            "exam_year": 2567,
            "appointed_count": 73,
            "remaining_count": 44,
            "evidence": "listed 117 | appointed 73 | remaining 44",
            "source_url": "https://personnel.obec.go.th/example.pdf",
            "announced_at": None,
        },
        {
            "area_key": "agree",
            "area_name": "สพป.ปีเดียวกัน",
            "exam_year": 2567,
            "appointed_count": 11,
            "remaining_count": 0,
            "evidence": "listed 11 | appointed 11 | remaining 0",
            "source_url": "file:///balance-67.pdf",
            "announced_at": None,
        },
    ]
    review = evidence_review(areas, areas, 2569, events)
    decision = review["decision"]
    assert [item["area_name"] for item in decision["primary"]] == ["สพป.ปีเดียวกัน"]
    assert [item["area_name"] for item in decision["targets"]] == ["สพป.ปีเดียวกัน"]
    assert "สพป.นครราชสีมา เขต 1" in [item["area_name"] for item in decision["backups"]]
    shown = {item["area_name"]: item["status"] for item in decision["rows"]}
    assert set(shown) == {area.area_name for area in areas}
    assert shown["สพป.แหล่งเดียว"] != "ตัวเลือกหลักเบื้องต้น"
    assert shown["สพป.นครราชสีมา เขต 2"] == "เรียกบรรจุน้อย"
    assert shown["สพป.นครราชสีมา เขต 1"] == "ไม่เน้นเพราะเป็นเขต 1"
    assert shown["สพป.ขัดแย้ง"] == "มีข้อมูลขัดแย้ง"
    target = decision["targets"][0]
    assert "ปี 2567" in target["facts"]
    assert "44/44" not in target["facts"]
    assert "ข้อเท็จจริง" not in target["reason"]
    assert "โอกาสสอบติด" in decision["note"]
    assert "2026-10-17" not in target["facts"]
    assert any("ผู้สมัคร" in line for line in decision["change_if"])
    scored = score_areas(areas, 2569)
    assert all(component.key != "account" for item in scored for component in item.components)


def test_optional_choice_filters_do_not_mutate_the_source_area_list():
    zone_one = _area("สพป.นครราชสีมา เขต 1", "z1", openings=2, listed=10, appointed=2)
    strong = _area("สพป.นครราชสีมา เขต 2", "z2", openings=2, listed=10, appointed=8)
    areas = [zone_one, strong]

    filtered = apply_choice_filters(areas, exclude_zone_one=True, exclude_low_history=True)

    assert [item.area_name for item in filtered] == ["สพป.นครราชสีมา เขต 2"]
    assert [item.area_name for item in areas] == ["สพป.นครราชสีมา เขต 1", "สพป.นครราชสีมา เขต 2"]
