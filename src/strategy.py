from __future__ import annotations

from src.labels import CONFIDENCE_READY, MISSING
from src.scoring import AreaInput, AreaScore
from src.staffing import staffing_metrics


def choice_notes(area: AreaInput, score: AreaScore | None, focus_year: int) -> dict:
    stat = area.years.get(focus_year)
    metrics = staffing_metrics(area.staffing_actual, area.staffing_standard)
    pros = []
    risks = []
    if stat and stat.opened and stat.openings:
        pros.append(f"ปี {focus_year} เปิดรับ {stat.openings} ตำแหน่ง")
    else:
        risks.append(f"ปี {focus_year} ยังไม่มีจำนวนตำแหน่งที่เปิดรับ")
    if stat and stat.applicants_known and stat.openings:
        pros.append(f"มีผู้สมัคร {stat.applicants} คน คิดเป็น 1 : {stat.applicants / stat.openings:.1f}")
    else:
        risks.append("ยังไม่มีจำนวนผู้สมัคร จึงยังไม่รู้การแข่งขันจริง")
    if score is not None:
        call = next((item for item in score.components if item.key == "call_ratio"), None)
        if call and call.score is not None:
            pros.append(call.reason)
        else:
            risks.append("ไม่มีประวัติเรียกบรรจุที่ใช้เทียบความลึกของบัญชี")
    if metrics["shortage"]:
        risks.append(metrics["reason"])
    elif metrics["gap"] is not None and metrics["gap"] >= 0:
        pros.append(metrics["reason"])
    else:
        risks.append("ไม่มีอัตรากำลังรายเขตที่ยืนยัน")
    risks.append("การขาดครูไม่ได้แปลว่าเขตจะเปิดสอบหรือเรียกบรรจุถึงทุกคน")
    return {"pros": pros, "risks": risks}


def decision_gaps(area: AreaInput, focus_year: int) -> list[str]:
    """Say which real input is missing and which later fact could move the rank."""
    stat = area.years.get(focus_year)
    gaps = []
    if stat is None or not stat.applicants_known:
        gaps.append(
            "ยังไม่พบจำนวนผู้สมัครเอกภาษาอังกฤษของปีนี้ ควรรอตัวเลขผู้สมัครจากประกาศหรือเว็บเขต "
            "เมื่อมีตัวเลขจริง ระบบจะคำนวณผู้สมัครต่อหนึ่งตำแหน่งใหม่ และอันดับอาจเปลี่ยน"
        )
    if not any(
        item.listed_count and item.appointed_count is not None and 0 <= item.appointed_count <= item.listed_count
        for item in area.years.values()
    ):
        gaps.append("ยังไม่มีประวัติเรียกบรรจุที่ใช้ได้ ถ้าพบประกาศที่ระบุจำนวนผู้ถูกเรียกหรือลำดับที่เรียกถึง จะนำมาประกอบอันดับ")
    if area.staffing_actual is None or area.staffing_standard in (None, 0):
        gaps.append("ยังไม่มีอัตรากำลังรายเขตที่ยืนยัน การขาดครูไม่ถูกใช้เป็นเหตุผลเดียวในการจัดอันดับ")
    else:
        gaps.append("การขาดครูเป็นเพียงองค์ประกอบเสริม น้ำหนักถูกจำกัด และไม่ได้แปลว่าอันดับสูงคือโอกาสสอบติด")
    return gaps


def classify_choices(areas: list[AreaInput], scores: list[AreaScore], coverage: list[dict], focus_year: int) -> dict[str, list[dict]]:
    by_key = {item.area_key: item for item in scores}
    groups = {"A": [], "B": [], "C": [], "D": []}
    seen = set()
    for area in areas:
        score = by_key.get(area.area_key)
        seen.add(area.area_name)
        row = _row(area.area_name, area.detail_url or area.source_url, score, area, focus_year)
        if score is None or score.total is None:
            if area.coverage_status in {"รอประกาศ", "ประกาศแล้ว", "เปิดรับสมัคร"}:
                groups["C"].append(row)
            else:
                groups["D"].append(row)
            continue
        if score.confidence == CONFIDENCE_READY:
            groups["A"].append(row)
        else:
            groups["B"].append(row)
    for item in coverage:
        if item["area_name"] in seen:
            continue
        bucket = "C" if item["status"] in {"รอประกาศ", "ประกาศแล้ว", "เปิดรับสมัคร"} else "D"
        groups[bucket].append(
            {
                "เขต": item["area_name"],
                "เหตุผล": item["evidence"],
                "แหล่ง": item.get("source_url") or "",
                "ความมั่นใจ": MISSING,
            }
        )
    return groups


def _row(name: str, source: str | None, score: AreaScore | None, area: AreaInput, focus_year: int) -> dict:
    notes = choice_notes(area, score, focus_year)
    return {
        "เขต": name,
        "ความมั่นใจ": score.confidence if score and score.total is not None else MISSING,
        "คะแนน": f"{score.total:.1f}" if score and score.total is not None else MISSING,
        "เหตุผล": score.summary if score else MISSING,
        "ข้อดี": " ".join(notes["pros"]),
        "ความเสี่ยง": " ".join(notes["risks"]),
        "แหล่ง": source or "",
    }
