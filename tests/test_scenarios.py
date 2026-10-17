from src.labels import MISSING, SIMULATION
from src.scenarios import applicant_increase, future_calls, reach_rank, target_disadvantage
from src.scoring import AreaInput, YearStat


def sample_areas():
    first = AreaInput(
        "a",
        "เขตเอ",
        "สพป.",
        {
            2569: YearStat(2569, True, 4, 40, True, None, None, None),
            2567: YearStat(2567, True, 2, 20, True, 10, 4, 6),
            2566: YearStat(2566, True, 2, 10, True, 10, 8, 2),
        },
    )
    second = AreaInput(
        "b",
        "เขตบี",
        "สพม.",
        {
            2569: YearStat(2569, True, 2, 10, True, None, None, None),
            2567: YearStat(2567, True, 1, 5, True, 10, 9, 1),
        },
    )
    return [first, second]


def test_scenario_applicant_increase_changes_ratio_only():
    result = applicant_increase(sample_areas(), 2569, 50)
    assert result.kind == "applicants"
    row = next(item for item in result.rows if item["เขต"] == "เขตเอ")
    assert row["ผู้สมัครเดิม"] == 40
    assert row["ผู้สมัครจำลอง"] == 60
    assert row["อัตราจำลอง"] == "1 : 15.0"
    assert SIMULATION in result.limitation


def test_scenario_applicant_increase_without_baseline():
    area = AreaInput("c", "เขตซี", "สพป.", {2569: YearStat(2569, True, 2, None, False, None, None, None)})
    result = applicant_increase([area], 2569, 10)
    assert result.scores == []
    assert MISSING in result.message


def test_scenario_future_calls_low_mid_high():
    result = future_calls(sample_areas(), list_size=10)
    row = next(item for item in result.rows if item["เขต"] == "เขตเอ")
    assert row["กรณีต่ำ"] == "40%"
    assert row["กรณีกลาง"] == "60%"
    assert row["กรณีสูง"] == "80%"
    assert row["เรียกต่ำ"] == 4
    assert row["เรียกสูง"] == 8
    single = next(item for item in result.rows if item["เขต"] == "เขตบี")
    assert "1 ปี" in single["หมายเหตุ"]


def test_target_comparison_marks_missing_backups():
    result = target_disadvantage(sample_areas(), 2569, "a", applicant_percent=10)
    roles = [row["บทบาท"] for row in result.comparison]
    assert roles[0] == "เขตเป้าหมาย"
    assert "เขตสำรอง" in roles
    assert any(row["เขต"] == MISSING for row in result.comparison)
    assert "ไม่ถูกเขียนทับ" in result.message


def test_scenario_target_can_change_rank():
    result = target_disadvantage(sample_areas(), 2569, "b", applicant_percent=0, openings_override=1, others_percent=0)
    assert "อันดับเดิม" in result.message
    assert result.scores


def test_scenario_reach_does_not_claim_certainty():
    area = sample_areas()[0]
    result = reach_rank(area, my_rank=7, list_size=10, known_appointed=3)
    assert SIMULATION in result.limitation
    sources = [row["แหล่ง"] for row in result.rows]
    assert "ตัวเลขที่กรอก" in sources
    assert any("กรณีต่ำ" in source for source in sources)
    low = next(row for row in result.rows if "กรณีต่ำ" in row["แหล่ง"])
    assert low["เรียกถึงลำดับ"] == 4
    assert "ลึกกว่า" in low["เทียบกับลำดับที่กรอก"]


def test_scenario_reach_without_history():
    area = AreaInput("z", "เขตไม่มีประวัติ", "สพป.", {2569: YearStat(2569, True, 1, None, False, None, None, None)})
    result = reach_rank(area, 2, 10)
    assert MISSING in result.message
    assert result.rows == []
