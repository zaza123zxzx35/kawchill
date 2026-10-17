from __future__ import annotations

from copy import deepcopy
from math import ceil, floor
from dataclasses import dataclass, field
from statistics import median

from src.labels import MISSING, SIMULATION
from src.scoring import AreaInput, AreaScore, YearStat, call_ratios, score_areas
from src.staffing import staffing_metrics


@dataclass
class ScenarioResult:
    title: str
    kind: str
    assumption: str
    limitation: str
    message: str
    scores: list[AreaScore]
    rows: list[dict]
    comparison: list[dict] = field(default_factory=list)


def _copy_areas(areas: list[AreaInput]) -> list[AreaInput]:
    return deepcopy(areas)


def _find(areas: list[AreaInput], area_key: str) -> AreaInput | None:
    return next((area for area in areas if area.area_key == area_key), None)


def applicant_increase(areas: list[AreaInput], focus_year: int, percent: float, target_key: str | None = None) -> ScenarioResult:
    title = "จำนวนผู้สมัครเพิ่ม"
    assumption = f"สมมติว่าจำนวนผู้สมัครในปี {focus_year} เพิ่ม {percent:g}% จากตัวเลขที่มีจริง แล้วคำนวณอัตราการแข่งขันใหม่"
    limitation = f"{SIMULATION} อดีตและตัวคูณนี้ไม่ได้รับประกันจำนวนผู้สมัครจริงในอนาคต"
    usable = [
        area
        for area in areas
        if (stat := area.years.get(focus_year)) and stat.applicants_known and stat.applicants is not None and stat.openings
    ]
    if not usable:
        return ScenarioResult(title, "applicants", assumption, limitation, f"ปี {focus_year}: {MISSING} สำหรับฐานจำนวนผู้สมัคร", [], [])
    cloned = _copy_areas(areas)
    rows = []
    for area in cloned:
        stat = area.years.get(focus_year)
        if stat is None or not stat.applicants_known or stat.applicants is None or not stat.openings:
            continue
        original = stat.applicants
        updated = int(round(original * (1 + percent / 100)))
        stat.applicants = updated
        rows.append(
            {
                "เขต": area.area_name,
                "ผู้สมัครเดิม": original,
                "ผู้สมัครจำลอง": updated,
                "อัตราเดิม": f"1 : {original / stat.openings:.1f}",
                "อัตราจำลอง": f"1 : {updated / stat.openings:.1f}",
                "ผลต่างอัตรากำลัง": _staffing_fact(area),
            }
        )
    before = score_areas(areas, focus_year)
    scores = score_areas(cloned, focus_year)
    before_rank = {item.area_name: item.rank for item in before}
    after_rank = {item.area_name: item.rank for item in scores}
    for row in rows:
        row["อันดับฐาน"] = _rank_text(before_rank.get(row["เขต"]))
        row["อันดับตามสมมติฐาน"] = _rank_text(after_rank.get(row["เขต"]))
    chosen = target_key or usable[0].area_key
    note = "" if target_key else " ตารางเทียบใช้เขตแรกที่มีฐานผู้สมัครเป็นเขตเป้าหมายตัวอย่าง"
    message = "คำนวณจากจำนวนผู้สมัครที่มีอยู่จริงแล้วคูณตามสมมติฐาน จำนวนครูจริงไม่ถูกเปลี่ยนในการจำลองนี้ และข้อมูลในฐานไม่ถูกเขียนทับ" + note
    return ScenarioResult(title, "applicants", assumption, limitation, message, scores, rows, compare_with_backups(before, scores, chosen))


def future_calls(areas: list[AreaInput], list_size: int | None = None) -> ScenarioResult:
    title = "การเรียกบรรจุในอนาคต"
    assumption = "กรณีต่ำใช้สัดส่วนเรียกบรรจุน้อยสุด กรณีกลางใช้ค่ามัธยฐาน และกรณีสูงใช้สัดส่วนมากสุด จากปีที่มีทั้งจำนวนขึ้นบัญชีและจำนวนเรียกบรรจุ"
    limitation = f"{SIMULATION} สัดส่วนในอดีตไม่ได้แปลว่าบัญชีรอบใหม่จะถูกเรียกในอัตราเดิม และไม่ได้แปลว่าคนที่อยู่ลำดับท้ายจะถูกเรียกครบ"
    rows = []
    any_history = False
    for area in areas:
        ratios = call_ratios(area)
        if not ratios:
            rows.append(
                {
                    "เขต": area.area_name,
                    "กรณีต่ำ": MISSING,
                    "กรณีกลาง": MISSING,
                    "กรณีสูง": MISSING,
                    "ผลต่างอัตรากำลัง": _staffing_fact(area),
                    "หมายเหตุ": "ไม่มีประวัติเรียกบรรจุ ผลต่างอัตรากำลังเป็นข้อมูลจริงและไม่ได้แปลงเป็นสัดส่วนเรียกบรรจุ",
                }
            )
            continue
        any_history = True
        values = [value for _year, value in ratios]
        low, mid, high = min(values), float(median(values)), max(values)
        note = "มีประวัติเพียง 1 ปี ช่วงต่ำกลางสูงจึงเป็นค่าเดียวกัน" if len(values) == 1 else f"ใช้ {len(values)} ปี"
        row = {
            "เขต": area.area_name,
            "กรณีต่ำ": f"{low:.0%}",
            "กรณีกลาง": f"{mid:.0%}",
            "กรณีสูง": f"{high:.0%}",
            "ผลต่างอัตรากำลัง": _staffing_fact(area),
            "หมายเหตุ": note + " ผลต่างอัตรากำลังเป็นข้อมูลจริง ไม่ได้ใช้ตัดสินสัดส่วนเรียกบรรจุ",
        }
        if list_size is not None and list_size > 0:
            row["เรียกต่ำ"] = int(round(low * list_size))
            row["เรียกกลาง"] = int(round(mid * list_size))
            row["เรียกสูง"] = int(round(high * list_size))
            row["หมายเหตุ"] = note + f" หากสมมติว่ามีคนในบัญชี {list_size} คน ผลต่างอัตรากำลังเป็นข้อมูลจริง ไม่ได้ใช้ตัดสินสัดส่วนเรียกบรรจุ"
        rows.append(row)
    message = "แสดงช่วงจากประวัติจริง และไม่ได้เขียนทับจำนวนเรียกบรรจุที่บันทึกไว้" if any_history else MISSING
    return ScenarioResult(title, "future", assumption, limitation, message, [], rows)


def target_disadvantage(
    areas: list[AreaInput],
    focus_year: int,
    target_key: str,
    applicant_percent: float = 0,
    openings_override: int | None = None,
    others_percent: float = 0,
) -> ScenarioResult:
    title = "เขตเป้าหมายเสียเปรียบ"
    assumption = (
        f"สมมติให้เขตเป้าหมายมีผู้สมัครเพิ่ม {applicant_percent:g}%"
        + (f" และตำแหน่งเปิดรับเป็น {openings_override}" if openings_override is not None else "")
        + (f" ขณะที่เขตอื่นมีผู้สมัครเพิ่ม {others_percent:g}%" if others_percent else "")
    )
    limitation = f"{SIMULATION} ใช้เฉพาะเมื่อมีฐานจำนวนจริงสำหรับค่าที่ปรับ การเปลี่ยนสมมติฐานไม่ถูกบันทึกทับข้อมูลต้นทาง"
    target = _find(areas, target_key)
    if target is None:
        return ScenarioResult(title, "target", assumption, limitation, MISSING, [], [])
    before = score_areas(areas, focus_year)
    before_rank = next((item.rank for item in before if item.area_key == target_key), None)
    cloned = _copy_areas(areas)
    notes = []
    for area in cloned:
        stat = area.years.get(focus_year)
        if stat is None:
            continue
        percent = applicant_percent if area.area_key == target_key else others_percent
        if percent and area.area_key == target_key or (percent and area.area_key != target_key):
            if stat.applicants_known and stat.applicants is not None:
                stat.applicants = int(round(stat.applicants * (1 + percent / 100)))
            elif area.area_key == target_key and applicant_percent:
                notes.append("ยังไม่มีฐานผู้สมัครของเขตเป้าหมาย จึงไม่จำลองการเพิ่มผู้สมัคร")
        if area.area_key == target_key and openings_override is not None:
            if openings_override < 0:
                notes.append("จำนวนตำแหน่งที่กำหนดติดลบ จึงไม่ใช้ค่านั้น")
            else:
                stat.openings = openings_override
                stat.opened = openings_override > 0
    after = score_areas(cloned, focus_year)
    after_rank = next((item.rank for item in after if item.area_key == target_key), None)
    before_text = str(before_rank) if before_rank else MISSING
    after_text = str(after_rank) if after_rank else MISSING
    message = f"อันดับเดิม {before_text} อันดับตามสมมติฐาน {after_text}"
    if notes:
        message += " " + " ".join(notes)
    before_by = {item.area_key: item for item in before}
    rows = []
    for item in after:
        base = before_by.get(item.area_key)
        rows.append(
            {
                "เขต": item.area_name,
                "อันดับฐาน": _rank_text(base.rank if base else None),
                "อันดับจำลอง": _rank_text(item.rank),
                "คะแนนฐาน": f"{base.total:.1f}" if base and base.total is not None else MISSING,
                "คะแนนจำลอง": f"{item.total:.1f}" if item.total is not None else MISSING,
                "ผลต่างอัตรากำลัง": _staffing_fact(next(area for area in areas if area.area_key == item.area_key)),
                "เหตุผล": item.summary,
            }
        )
    message += " ข้อมูลในฐานไม่ถูกเขียนทับ"
    return ScenarioResult(title, "target", assumption, limitation, message, after, rows, compare_with_backups(before, after, target_key))


def reach_rank(
    area: AreaInput | None,
    my_rank: int | None,
    list_size: int | None,
    known_appointed: int | None = None,
) -> ScenarioResult:
    title = "โอกาสเรียกถึงลำดับของเรา"
    assumption = "นำสัดส่วนเรียกบรรจุต่ำสุด มัธยฐาน และสูงสุดจากประวัติจริง ไปคูณกับจำนวนคนในบัญชีที่กรอก"
    limitation = f"{SIMULATION} ไม่ใช่ความน่าจะเป็นที่ผ่านการรับรอง และไม่มีทางรู้ล่วงหน้าว่าจะเรียกถึงลำดับที่กรอกแน่นอน"
    if area is None or my_rank is None or list_size is None:
        return ScenarioResult(title, "reach", assumption, limitation, "กรอกอันดับของตนเองและจำนวนคนในบัญชี", [], [])
    if my_rank < 1 or list_size < 1 or my_rank > list_size:
        return ScenarioResult(title, "reach", assumption, limitation, "อันดับต้องอยู่ระหว่าง 1 กับจำนวนคนในบัญชี", [], [])
    if known_appointed is not None and (known_appointed < 0 or known_appointed > list_size):
        return ScenarioResult(title, "reach", assumption, limitation, "จำนวนที่เรียกบรรจุแล้วต้องอยู่ระหว่าง 0 กับจำนวนคนในบัญชี", [], [])

    rows = []
    ratios = call_ratios(area)
    if known_appointed is not None:
        reached = known_appointed >= my_rank
        rows.append(
            {
                "แหล่ง": "ตัวเลขที่กรอก",
                "เรียกถึงลำดับ": known_appointed,
                "เทียบกับลำดับที่กรอก": "ลำดับที่กรอกอยู่ในจำนวนที่เรียกแล้ว" if reached else "ลำดับที่กรอกยังลึกกว่าจำนวนที่เรียกแล้ว",
            }
        )
    if not ratios:
        return ScenarioResult(
            title,
            "reach",
            assumption,
            limitation,
            f"ช่วงต่ำกลางสูง: {MISSING} เพราะเขตนี้ไม่มีประวัติเรียกบรรจุที่ใช้คำนวณ",
            [],
            rows,
        )
    values = [value for _year, value in ratios]
    low, mid, high = min(values), float(median(values)), max(values)
    note = "มีประวัติเพียง 1 ปี" if len(values) == 1 else f"ใช้ประวัติ {len(values)} ปี"
    for name, ratio in (("ต่ำ", low), ("กลาง", mid), ("สูง", high)):
        reach = int(round(ratio * list_size))
        rows.append(
            {
                "แหล่ง": f"{SIMULATION} กรณี{name}",
                "เรียกถึงลำดับ": reach,
                "เทียบกับลำดับที่กรอก": "ลำดับที่กรอกอยู่ในช่วงประมาณการนี้" if reach >= my_rank else "ลำดับที่กรอกอยู่ลึกกว่าช่วงประมาณการนี้",
                "สัดส่วน": f"{ratio:.0%}",
                "หมายเหตุ": note,
            }
        )
    metrics = staffing_metrics(area.staffing_actual, area.staffing_standard)
    if metrics["gap"] is not None:
        rows.append(
            {
                "แหล่ง": "ข้อมูลจริง อัตรากำลัง",
                "เรียกถึงลำดับ": MISSING,
                "เทียบกับลำดับที่กรอก": metrics["reason"],
                "สัดส่วน": MISSING,
                "หมายเหตุ": "ผลต่างอัตรากำลังไม่ถูกแปลงเป็นความน่าจะเป็น",
            }
        )
    baseline = known_appointed if known_appointed is not None else None
    message = note + " ผลนี้เป็นการประมาณการเชิงสถานการณ์จากฐานที่กรอกและประวัติที่บันทึกไว้ ไม่ได้เขียนทับข้อมูลจริง"
    if baseline is not None:
        message += f" จำนวนที่กรอกว่าเรียกแล้วคือ {baseline} คน"
    return ScenarioResult(title, "reach", assumption, limitation, message, [], rows)


def compare_with_backups(before: list[AreaScore], after: list[AreaScore], target_key: str, limit: int = 3) -> list[dict]:
    after_by = {item.area_key: item for item in after}
    target = next((item for item in before if item.area_key == target_key), None)
    if target is None:
        return []
    backups = [item for item in before if item.area_key != target_key and item.total is not None]
    backups.sort(key=lambda item: (item.rank or 10**6, item.area_name))
    chosen = backups[:limit]
    rows = [_comparison_row("เขตเป้าหมาย", target, after_by.get(target.area_key))]
    for item in chosen:
        rows.append(_comparison_row("เขตสำรอง", item, after_by.get(item.area_key)))
    if len(chosen) < limit:
        rows.append(
            {
                "บทบาท": "เขตสำรอง",
                "เขต": MISSING,
                "อันดับฐาน": MISSING,
                "อันดับตามสมมติฐาน": MISSING,
                "คะแนนฐาน": MISSING,
                "คะแนนตามสมมติฐาน": MISSING,
                "หมายเหตุ": f"มีเขตที่ให้คะแนนได้เพียง {len(chosen)} เขต นอกจากเขตเป้าหมาย",
            }
        )
    return rows


def _comparison_row(role: str, before: AreaScore, after: AreaScore | None) -> dict:
    return {
        "บทบาท": role,
        "เขต": before.area_name,
        "อันดับฐาน": _rank_text(before.rank),
        "อันดับตามสมมติฐาน": _rank_text(after.rank if after else None),
        "คะแนนฐาน": f"{before.total:.1f}" if before.total is not None else MISSING,
        "คะแนนตามสมมติฐาน": f"{after.total:.1f}" if after and after.total is not None else MISSING,
        "หมายเหตุ": "",
    }


def _rank_text(rank: int | None) -> str:
    return str(rank) if rank else MISSING


def staffing_change(areas: list[AreaInput], focus_year: int, percent: float, weights: dict | None = None, target_key: str | None = None) -> ScenarioResult:
    title = "อัตรากำลังครูเปลี่ยน"
    assumption = f"สมมติว่าจำนวนครูที่มีจริงเปลี่ยน {percent:g}% จากตัวเลขอัตรากำลังที่บันทึกไว้ เกณฑ์ไม่เปลี่ยน"
    limitation = f"{SIMULATION} การเปลี่ยนจำนวนครูในแบบจำลองไม่ได้แปลว่าตำแหน่งสอบจะเปลี่ยนตาม และไม่ได้เขียนทับฐานข้อมูล"
    if not any(area.staffing_actual is not None and area.staffing_standard not in (None, 0) for area in areas):
        return ScenarioResult(title, "staffing", assumption, limitation, f"อัตรากำลัง: {MISSING}", [], [])
    before = score_areas(areas, focus_year, weights)
    cloned = _copy_areas(areas)
    rows = []
    for area in cloned:
        if area.staffing_actual is None or area.staffing_standard in (None, 0):
            continue
        original = area.staffing_actual
        area.staffing_actual = max(0, int(round(original * (1 + percent / 100))))
        gap = area.staffing_actual - area.staffing_standard
        rows.append({"เขต": area.area_name, "ครูจริงเดิม": original, "ครูจริงจำลอง": area.staffing_actual, "ผลต่างจำลอง": gap})
    after = score_areas(cloned, focus_year, weights)
    before_rank = {item.area_name: item.rank for item in before}
    after_rank = {item.area_name: item.rank for item in after}
    for row in rows:
        row["อันดับฐาน"] = _rank_text(before_rank.get(row["เขต"]))
        row["อันดับตามสมมติฐาน"] = _rank_text(after_rank.get(row["เขต"]))
    return ScenarioResult(
        title,
        "staffing",
        assumption,
        limitation,
        "คำนวณจากจำนวนครูจริงที่บันทึกไว้ ข้อมูลในฐานไม่ถูกเขียนทับ",
        after,
        rows,
        compare_with_backups(before, after, target_key or (areas[0].area_key if areas else "")),
    )


def factor_outlook(area: AreaInput) -> list[dict]:
    """Mark which outlook numbers are stored facts and which are assumptions."""
    rows = []
    stat = next((item for item in area.years.values() if item.applicants_known and item.openings), None)
    if stat is None:
        rows.append({"ปัจจัย": "ผู้สมัคร", "ฐานจริง": MISSING, "ต่ำ": MISSING, "กลาง": MISSING, "สูง": MISSING, "ชนิด": "ข้อมูลจริงไม่พอ"})
    else:
        rows.append(
            {
                "ปัจจัย": f"ผู้สมัครปี {stat.exam_year}",
                "ฐานจริง": stat.applicants,
                "ต่ำ": int(round(stat.applicants * 1.1)),
                "กลาง": int(round(stat.applicants * 1.2)),
                "สูง": int(round(stat.applicants * 1.5)),
                "ชนิด": "ฐานจริง คูณ 10/20/50% เป็นสมมติฐาน",
            }
        )
    current = next((item for year, item in sorted(area.years.items(), reverse=True) if item.opened and item.openings), None)
    if current is None:
        rows.append({"ปัจจัย": "ตำแหน่งเปิดรับ", "ฐานจริง": MISSING, "ต่ำ": MISSING, "กลาง": MISSING, "สูง": MISSING, "ชนิด": "ข้อมูลจริงไม่พอ"})
    else:
        rows.append(
            {
                "ปัจจัย": f"ตำแหน่งปี {current.exam_year}",
                "ฐานจริง": current.openings,
                "ต่ำ": max(0, floor(current.openings * 0.8)),
                "กลาง": current.openings,
                "สูง": ceil(current.openings * 1.2),
                "ชนิด": "กลางเป็นข้อมูลจริง ต่ำและสูงเป็นสมมติฐาน",
            }
        )
    ratios = call_ratios(area)
    if not ratios:
        rows.append({"ปัจจัย": "สัดส่วนเรียกบรรจุ", "ฐานจริง": MISSING, "ต่ำ": MISSING, "กลาง": MISSING, "สูง": MISSING, "ชนิด": "ข้อมูลจริงไม่พอ"})
    else:
        values = [value for _year, value in ratios]
        rows.append(
            {
                "ปัจจัย": "สัดส่วนเรียกบรรจุจากประวัติ",
                "ฐานจริง": f"{sum(values) / len(values):.0%}",
                "ต่ำ": f"{min(values):.0%}",
                "กลาง": f"{float(median(values)):.0%}",
                "สูง": f"{max(values):.0%}",
                "ชนิด": "ค่าจากประวัติจริง ไม่ใช่คำพยากรณ์",
            }
        )
    if area.staffing_actual is None or area.staffing_standard in (None, 0):
        rows.append({"ปัจจัย": "ผลต่างอัตรากำลัง", "ฐานจริง": MISSING, "ต่ำ": MISSING, "กลาง": MISSING, "สูง": MISSING, "ชนิด": "ข้อมูลจริงไม่พอ"})
    else:
        actual = area.staffing_actual
        standard = area.staffing_standard
        rows.append(
            {
                "ปัจจัย": "ผลต่างครูจริง-เกณฑ์",
                "ฐานจริง": actual - standard,
                "ต่ำ": max(0, int(round(actual * 0.9))) - standard,
                "กลาง": actual - standard,
                "สูง": int(round(actual * 1.1)) - standard,
                "ชนิด": "กลางเป็นข้อมูลจริง ต่ำและสูงสมมติว่าครูจริงเปลี่ยน 10%",
            }
        )
    return rows


def _staffing_fact(area: AreaInput) -> str:
    metrics = staffing_metrics(area.staffing_actual, area.staffing_standard)
    if metrics["gap"] is None:
        return MISSING
    return f"{metrics['gap']:+d} ระดับ{metrics['level']} ข้อมูลจริง"


def ensure_year(area: AreaInput, year: int) -> YearStat:
    stat = area.years.get(year)
    if stat is None:
        stat = YearStat(year, None, None, None, False, None, None, None)
        area.years[year] = stat
    return stat
