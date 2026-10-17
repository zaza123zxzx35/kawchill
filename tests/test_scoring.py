from src.labels import MISSING
from src.scoring import AreaInput, YearStat, score_areas


def area(key, name, years, actual=None, standard=None):
    return AreaInput(key, name, "สพป.", {year.exam_year: year for year in years}, actual, standard)


def test_competition_and_history_produce_a_score():
    first = area(
        "a",
        "เขตเอ",
        [YearStat(2569, True, 4, 40, True, None, None, None), YearStat(2567, True, 2, 20, True, 10, 8, 2)],
    )
    second = area(
        "b",
        "เขตบี",
        [YearStat(2569, True, 1, None, False, None, None, None)],
    )
    scored = score_areas([first, second], 2569)
    by_name = {item.area_name: item for item in scored}
    assert by_name["เขตเอ"].total is not None
    assert by_name["เขตบี"].total is None
    assert MISSING in by_name["เขตบี"].summary
    assert by_name["เขตเอ"].rank == 1


def test_partial_history_is_not_scored_as_complete():
    partial = area(
        "p",
        "เขตบางส่วน",
        [YearStat(2569, True, 4, None, False, None, None, None), YearStat(2567, True, 2, 20, True, 10, 5, 5)],
    )
    partial.reference_at = "2026-10-16T19:50+07:00"
    complete = area(
        "q",
        "เขตครบ",
        [YearStat(2569, True, 4, 40, True, None, None, None), YearStat(2567, True, 2, 20, True, 10, 5, 5)],
    )
    complete.reference_at = "2026-10-16T19:50+07:00"
    scored = {item.area_name: item for item in score_areas([partial, complete], 2569)}
    assert scored["เขตบางส่วน"].confidence == "บางส่วน"
    assert scored["เขตบางส่วน"].total is not None
    assert scored["เขตบางส่วน"].total < 100
    competition = next(item for item in scored["เขตบางส่วน"].components if item.key == "competition")
    assert competition.score is None
    assert scored["เขตครบ"].confidence == "ใช้เทียบได้"
    assert scored["เขตครบ"].total > scored["เขตบางส่วน"].total


def test_missing_history_and_applicants_are_not_scored():
    lone = area("c", "เขตซี", [YearStat(2569, True, 3, None, False, None, None, None)])
    scored = score_areas([lone], 2569)
    assert scored[0].total is None
    assert scored[0].summary.startswith("ข้อมูลไม่เพียงพอ")


def test_closed_year_is_not_ranked():
    closed = area("d", "เขตดี", [YearStat(2568, False, None, None, False, None, None, None)])
    scored = score_areas([closed], 2568)
    assert scored[0].total is None
    assert "ไม่เปิดสอบ" in scored[0].summary


def test_missing_applicants_do_not_score_as_easy_competition():
    known = area(
        "known",
        "เขตมีผู้สมัคร",
        [YearStat(2569, True, 4, 8, True, None, None, None), YearStat(2567, True, 2, 20, True, 10, 5, 5)],
    )
    unknown = area(
        "unknown",
        "เขตไม่มีผู้สมัคร",
        [YearStat(2569, True, 4, None, False, None, None, None), YearStat(2567, True, 2, 20, True, 10, 5, 5)],
    )
    scored = {item.area_name: item for item in score_areas([known, unknown], 2569)}
    unknown_competition = next(item for item in scored["เขตไม่มีผู้สมัคร"].components if item.key == "competition")
    assert unknown_competition.score is None
    assert scored["เขตไม่มีผู้สมัคร"].total < scored["เขตมีผู้สมัคร"].total


def test_uneven_evidence_is_called_out():
    from src.scoring import evidence_notice

    staffed = area(
        "s",
        "เขตมีอัตรากำลัง",
        [YearStat(2569, True, 2, 10, True, None, None, None), YearStat(2567, True, 1, 5, True, 8, 4, 4)],
        10,
        12,
    )
    bare = area(
        "t",
        "เขตไม่มีอัตรากำลัง",
        [YearStat(2569, True, 2, 10, True, None, None, None), YearStat(2567, True, 1, 5, True, 8, 4, 4)],
    )
    notice = evidence_notice(score_areas([staffed, bare], 2569))
    assert notice is not None
    assert "ไม่เหมือนกัน" in notice


def test_remaining_is_not_replaced_when_call_exceeds_list():
    odd = area("e", "เขตอี", [YearStat(2567, True, 1, 5, True, 4, 9, None)])
    scored = score_areas([odd], 2567)
    ratios = [item for item in scored[0].components if item.key == "call_ratio"]
    assert ratios[0].score is None
    assert scored[0].total is not None
