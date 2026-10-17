from src.db import connect, init_db
from src.scenarios import future_calls, staffing_change
from src.scoring import SHORTAGE_CAP, AreaInput, YearStat, normalize_weights, score_areas
from src.service import _save_staffing
from src.staffing import link_staffing_records, staffing_metrics


def test_gap_is_actual_minus_standard_and_percent_needs_a_positive_standard():
    metrics = staffing_metrics(142, 157)
    assert metrics["gap"] == -15
    assert metrics["shortage"] == 15
    assert round(metrics["fill_percent"], 1) == 90.4
    assert metrics["level"] == "ขาดเล็กน้อย"
    zero = staffing_metrics(4, 0)
    assert zero["gap"] == 4
    assert zero["fill_percent"] is None
    assert zero["shortage"] is None
    assert staffing_metrics(None, 10)["level"] == "ข้อมูลไม่เพียงพอ"


def test_shortage_alone_does_not_create_a_rank():
    area = AreaInput("a", "เขตเอ", "สพป.", {2569: YearStat(2569, True, 3, None, False, None, None, None)}, 100, 150)
    scored = score_areas([area], 2569)
    assert scored[0].total is None
    shortage = next(item for item in scored[0].components if item.key == "shortage")
    assert shortage.score is not None
    assert shortage.weight <= SHORTAGE_CAP


def test_shortage_weight_cannot_exceed_the_cap():
    weights = normalize_weights({"shortage": 80, "openings": 10, "competition": 10})
    assert weights["shortage"] == SHORTAGE_CAP
    assert abs(sum(weights.values()) - 100) < 0.01


def test_staffing_shift_does_not_change_the_original_count():
    area = AreaInput(
        "a",
        "เขตเอ",
        "สพป.",
        {2569: YearStat(2569, True, 2, 20, True, None, None, None), 2567: YearStat(2567, True, 1, 10, True, 8, 4, 4)},
        100,
        120,
    )
    result = staffing_change([area], 2569, -10)
    assert area.staffing_actual == 100
    assert result.rows[0]["ครูจริงจำลอง"] == 90
    assert "ไม่ถูกเขียนทับ" in result.message


def test_refresh_keeps_one_row_and_records_the_old_value(tmp_path):
    db = tmp_path / "radar.db"
    init_db(db)
    item = {
        "scope": "area",
        "area_name": "สพป.ทดสอบ เขต 1",
        "subject": "ภาษาอังกฤษ",
        "actual_count": 10,
        "standard_count": 12,
        "gap_count": 99,
        "reference_at": "2026-09-26",
        "source_url": "https://example.test/report.php?r=2/9",
        "area_key": "2/9",
    }
    _save_staffing(db, "kruphuchuay", 1, item)
    changed = dict(item, actual_count=9)
    _save_staffing(db, "kruphuchuay", 2, changed)
    _save_staffing(db, "kruphuchuay", 3, dict(changed, actual_count=None, standard_count=None))
    with connect(db) as conn:
        rows = conn.execute("SELECT actual_count, standard_count, gap_count FROM staffing_records").fetchall()
        history = conn.execute("SELECT old_value, new_value FROM record_changes WHERE field_name='staffing_actual_count'").fetchall()
    assert len(rows) == 1
    assert rows[0]["actual_count"] == 9
    assert rows[0]["standard_count"] == 12
    assert rows[0]["gap_count"] == -3
    assert history[0]["old_value"] == "10"
    assert history[0]["new_value"] == "9"


def test_call_scenario_keeps_staffing_as_a_real_fact():
    area = AreaInput(
        "a",
        "เขตเอ",
        "สพป.",
        {2567: YearStat(2567, True, 1, 5, True, 10, 4, 6)},
        10,
        12,
    )
    result = future_calls([area])
    assert result.rows[0]["ผลต่างอัตรากำลัง"].startswith("-2")
    assert "ข้อมูลจริง" in result.rows[0]["ผลต่างอัตรากำลัง"]
    assert "ความน่าจะเป็น" not in result.message


def test_link_uses_exact_url_and_name(tmp_path):
    db = tmp_path / "radar.db"
    init_db(db)
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO recruitment_records (
                source_code, exam_year, exam_type, subject, affiliation, area_name, area_key, observed_at, detail_url
            ) VALUES ('kruphuchuay', 2569, 'general', 'ภาษาอังกฤษ', 'สพฐ.', 'สพป.ทดสอบ เขต 1', '2/9', '2026-10-17', 'https://example.test/report.php?r=2/9')
            """
        )
        conn.execute(
            """
            INSERT INTO staffing_records (
                source_code, scope, area_name, subject, actual_count, standard_count, gap_count, source_url, observed_at
            ) VALUES ('kruphuchuay', 'area', 'สพป.ทดสอบ เขต 1', 'ภาษาอังกฤษ', 10, 12, -2, 'https://example.test/report.php?r=2/9', '2026-10-17')
            """
        )
        conn.execute(
            """
            INSERT INTO staffing_records (
                source_code, scope, area_name, subject, actual_count, standard_count, gap_count, source_url, observed_at
            ) VALUES ('kruphuchuay', 'area', 'สพป.ชื่อคล้าย', 'ภาษาอังกฤษ', 1, 2, -1, 'https://example.test/other', '2026-10-17')
            """
        )
        linked = link_staffing_records(conn)
        exact = conn.execute("SELECT area_key FROM staffing_records WHERE area_name='สพป.ทดสอบ เขต 1'").fetchone()["area_key"]
        similar = conn.execute("SELECT area_key, verify_status FROM staffing_records WHERE area_name='สพป.ชื่อคล้าย'").fetchone()
    assert linked == 1
    assert exact == "2/9"
    assert similar["area_key"] is None
    assert similar["verify_status"] == "ยังจับคู่รหัสเขตไม่ได้"
