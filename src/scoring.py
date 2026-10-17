from __future__ import annotations

from dataclasses import dataclass, field

from src.labels import CONFIDENCE_PARTIAL, CONFIDENCE_READY, MISSING, NOT_OPEN
from src.parsing import format_thai_stamp
from src.staffing import shortage_score

WEIGHTS = {
    "openings": 20,
    "competition": 22,
    "call_ratio": 18,
    "shortage": 12,
    "staffing_trend": 8,
    "completeness": 12,
    "announcement": 8,
}
SHORTAGE_CAP = 20
EXAM_KEYS = {"competition", "call_ratio"}


@dataclass
class YearStat:
    exam_year: int
    opened: bool | None
    openings: int | None
    applicants: int | None
    applicants_known: bool
    listed_count: int | None
    appointed_count: int | None
    remaining_count: int | None = None


@dataclass
class AreaInput:
    area_key: str
    area_name: str
    org_level: str
    years: dict[int, YearStat]
    staffing_actual: int | None = None
    staffing_standard: int | None = None
    detail_url: str | None = None
    area_website: str | None = None
    source_url: str | None = None
    reference_at: str | None = None
    notice_url: str | None = None
    notice_confirmed: bool = False
    coverage_status: str | None = None
    staffing_reference_at: str | None = None
    staffing_source_url: str | None = None
    staffing_verified: str | None = None


@dataclass
class ComponentScore:
    key: str
    label: str
    score: float | None
    weight: float
    reason: str


@dataclass
class AreaScore:
    area_key: str
    area_name: str
    org_level: str
    total: float | None
    components: list[ComponentScore] = field(default_factory=list)
    summary: str = MISSING
    rank: int | None = None
    confidence: str = MISSING


def formula_text() -> str:
    return (
        "คะแนนเต็ม 100 ค่าเริ่มต้นคือตำแหน่งรับจริง 20 การแข่งขัน 22 ประวัติเรียกบรรจุ 18 "
        "การขาดครูภาษาอังกฤษไม่เกิน 20 ความครบของข้อมูล 12 สถานะประกาศ 8 และแนวโน้มอัตรากำลัง 8 "
        "ผู้ใช้ปรับน้ำหนักได้ แต่น้ำหนักการขาดครูไม่เกิน 20 คะแนนจาก 100 "
        "องค์ประกอบที่ไม่มีตัวเลขได้ 0 และยังอยู่ในตัวหาร "
        "จะให้คะแนนรวมเมื่อมีจำนวนผู้สมัครหรือประวัติเรียกบรรจุอย่างน้อยหนึ่งอย่าง "
        "การขาดครูไม่ทำให้เขตที่ไม่มีข้อมูลสอบถูกจัดอันดับ และไม่ได้แปลว่าจะเปิดสอบหรือเรียกบรรจุ "
        "ความมั่นใจระดับใช้เทียบได้ต้องมีทั้งจำนวนผู้สมัครและประวัติเรียกบรรจุ"
    )


def normalize_weights(raw: dict | None = None) -> dict[str, float]:
    source = raw or {}
    shortage = min(SHORTAGE_CAP, max(0.0, float(source.get("shortage", WEIGHTS["shortage"]))))
    others = {
        key: max(0.0, float(source.get(key, WEIGHTS[key])))
        for key in WEIGHTS
        if key != "shortage"
    }
    other_sum = sum(others.values())
    rest = 100 - shortage
    if other_sum <= 0:
        scaled = {key: 0.0 for key in others}
    else:
        scaled = {key: rest * value / other_sum for key, value in others.items()}
    scaled["shortage"] = shortage
    return scaled


def call_ratios(area: AreaInput) -> list[tuple[int, float]]:
    found = []
    for year, stat in sorted(area.years.items()):
        listed = stat.listed_count
        appointed = stat.appointed_count
        if listed is None or appointed is None or listed <= 0:
            continue
        if appointed < 0 or appointed > listed:
            continue
        found.append((year, appointed / listed))
    return found


def competition_ratio(stat: YearStat | None) -> float | None:
    if stat is None or not stat.applicants_known or stat.applicants is None or stat.openings is None or stat.openings <= 0:
        return None
    return stat.applicants / stat.openings


def score_areas(areas: list[AreaInput], focus_year: int, weights: dict | None = None) -> list[AreaScore]:
    active = normalize_weights(weights)
    focus_stats = []
    for area in areas:
        stat = area.years.get(focus_year)
        if stat and stat.opened and stat.openings is not None and stat.openings > 0:
            focus_stats.append(stat.openings)
    max_openings = max(focus_stats) if focus_stats else None
    ratios = []
    for area in areas:
        ratio = competition_ratio(area.years.get(focus_year))
        if ratio is not None:
            ratios.append(ratio)
    min_ratio = min(ratios) if ratios else None
    max_ratio = max(ratios) if ratios else None

    scored: list[AreaScore] = []
    for area in areas:
        scored.append(_score_one(area, focus_year, max_openings, min_ratio, max_ratio, active))
    ranked = [item for item in scored if item.total is not None]
    ranked.sort(key=lambda item: (-item.total, item.area_name))
    for index, item in enumerate(ranked, start=1):
        item.rank = index
    scored.sort(key=lambda item: (item.rank is None, item.rank or 10**6, item.area_name))
    return scored


def _score_one(
    area: AreaInput,
    focus_year: int,
    max_openings: int | None,
    min_ratio: float | None,
    max_ratio: float | None,
    weights: dict[str, float],
) -> AreaScore:
    stat = area.years.get(focus_year)
    if stat is None:
        return AreaScore(area.area_key, area.area_name, area.org_level, None, [], f"ปี {focus_year}: {MISSING}")
    if stat.opened is False:
        return AreaScore(area.area_key, area.area_name, area.org_level, None, [], f"ปี {focus_year}: {NOT_OPEN}")

    components: list[ComponentScore] = []
    openings_score = None
    if stat.openings is not None and stat.openings > 0 and max_openings:
        openings_score = 100 * stat.openings / max_openings
        components.append(
            ComponentScore(
                "openings",
                "ตำแหน่งเปิดรับ",
                openings_score,
                weights["openings"],
                f"ปี {focus_year} เปิด {stat.openings} ตำแหน่ง จากมากสุด {max_openings} ตำแหน่งในชุดที่เทียบ",
            )
        )
    else:
        components.append(ComponentScore("openings", "ตำแหน่งเปิดรับ", None, weights["openings"], MISSING))

    ratio = competition_ratio(stat)
    if ratio is None:
        components.append(
            ComponentScore(
                "competition",
                "อัตราการแข่งขัน",
                None,
                weights["competition"],
                f"ปี {focus_year} ยังไม่มีจำนวนผู้สมัครที่ใช้คำนวณได้",
            )
        )
    else:
        if max_ratio == min_ratio:
            competition_score = 100.0
        else:
            competition_score = 100 * (max_ratio - ratio) / (max_ratio - min_ratio)
        components.append(
            ComponentScore(
                "competition",
                "อัตราการแข่งขัน",
                competition_score,
                weights["competition"],
                f"ผู้สมัคร {stat.applicants} คนต่อ {stat.openings} ตำแหน่ง คิดเป็น 1 : {ratio:.1f}",
            )
        )

    ratios = call_ratios(area)
    if ratios:
        average = sum(value for _year, value in ratios) / len(ratios)
        years = ", ".join(str(year) for year, _value in ratios)
        components.append(
            ComponentScore(
                "call_ratio",
                "สัดส่วนเรียกบรรจุในอดีต",
                100 * average,
                weights["call_ratio"],
                f"ปี {years} เรียกบรรจุเฉลี่ย {average:.0%} ของผู้ขึ้นบัญชี สัดส่วนนี้ไม่ใช่ลำดับบัญชีที่เรียกถึง และไม่ได้รับประกันว่ารอบนี้จะเรียกเท่าเดิม",
            )
        )
    else:
        components.append(
            ComponentScore(
                "call_ratio",
                "ประวัติเรียกบรรจุ",
                None,
                weights["call_ratio"],
                "ไม่มีปีที่ทั้งจำนวนขึ้นบัญชีและจำนวนเรียกบรรจุครบ จึงไม่มีความลึกของบัญชีให้ใช้",
            )
        )

    short_score, short_reason = shortage_score(area.staffing_actual, area.staffing_standard)
    components.append(ComponentScore("shortage", "การขาดครูภาษาอังกฤษ", short_score, weights["shortage"], short_reason))
    components.append(ComponentScore("staffing_trend", "แนวโน้มอัตรากำลัง", None, weights["staffing_trend"], _trend_reason(area)))
    complete_score, complete_reason = _completeness_component(area, focus_year)
    components.append(ComponentScore("completeness", "ความครบถ้วนของข้อมูล", complete_score, weights["completeness"], complete_reason))
    announce_score, announce_reason = _announcement_component(area)
    components.append(ComponentScore("announcement", "สถานะประกาศ", announce_score, weights["announcement"], announce_reason))

    has_competition = any(item.key == "competition" and item.score is not None for item in components)
    has_history = any(item.key == "call_ratio" and item.score is not None for item in components)
    if not (has_competition or has_history):
        return AreaScore(
            area.area_key,
            area.area_name,
            area.org_level,
            None,
            components,
            "ข้อมูลไม่เพียงพอ เพราะยังไม่มีทั้งจำนวนผู้สมัครและประวัติเรียกบรรจุ การขาดครูอย่างเดียวไม่ถูกนำมาจัดอันดับ",
            None,
            MISSING,
        )

    total = sum((item.score or 0) * item.weight for item in components) / 100
    exam_missing = [item.label for item in components if item.key in EXAM_KEYS and item.score is None]
    other_missing = [item.label for item in components if item.key not in EXAM_KEYS and item.score is None]
    confidence = CONFIDENCE_PARTIAL if exam_missing else CONFIDENCE_READY
    reasons = [item.reason for item in components if item.score is not None]
    if exam_missing or other_missing:
        reasons.append("ยังไม่ให้คะแนนส่วนที่ขาด: " + ", ".join(exam_missing + other_missing))
    summary = f"ความมั่นใจ{confidence} " + " ".join(reasons)
    return AreaScore(area.area_key, area.area_name, area.org_level, total, components, summary, None, confidence)


def _history_years(area: AreaInput) -> list[int]:
    found = []
    for year, stat in sorted(area.years.items()):
        if stat.opened is False:
            continue
        has_applicants = stat.applicants_known and stat.applicants is not None
        listed = stat.listed_count
        appointed = stat.appointed_count
        has_calls = listed is not None and appointed is not None and listed > 0 and 0 <= appointed <= listed
        if has_applicants or has_calls:
            found.append(year)
    return found


def _trend_reason(area: AreaInput) -> str:
    if area.staffing_reference_at:
        return (
            f"มีอัตรากำลังเพียงชุดเดียว อ้างอิง {format_thai_stamp(area.staffing_reference_at)} "
            "จึงยังไม่บอกแนวโน้มย้อนหลัง"
        )
    return "ไม่มีอัตรากำลังหลายปี จึงไม่บอกแนวโน้ม"


def _completeness_component(area: AreaInput, focus_year: int) -> tuple[float, str]:
    stat = area.years.get(focus_year)
    points = 0
    found = []
    missing = []
    if stat and stat.openings is not None and stat.opened:
        points += 25
        found.append("ตำแหน่งเปิดรับ")
    else:
        missing.append("ตำแหน่งเปิดรับ")
    if stat and stat.applicants_known:
        points += 25
        found.append("ผู้สมัคร")
    else:
        missing.append("ผู้สมัคร")
    if _history_years(area):
        points += 20
        found.append(f"ประวัติ {len(_history_years(area))} ปี")
    else:
        missing.append("ประวัติเรียกบรรจุ")
    if area.staffing_actual is not None and area.staffing_standard not in (None, 0):
        points += 15
        found.append("อัตรากำลัง")
    else:
        missing.append("อัตรากำลัง")
    if area.reference_at or area.staffing_reference_at:
        points += 15
        found.append("วันที่อ้างอิง")
    else:
        missing.append("วันที่อ้างอิง")
    reason = "มี " + ", ".join(found) if found else "ยังไม่มีชิ้นข้อมูลหลัก"
    if missing:
        reason += " ขาด " + ", ".join(missing)
    return float(points), reason


def evidence_notice(scores: list[AreaScore]) -> str | None:
    """Warn when a rank mixes districts that are missing different inputs."""
    ranked = [item for item in scores if item.total is not None]
    if not ranked:
        return None
    missing_sets = [
        tuple(component.label for component in item.components if component.score is None)
        for item in ranked
    ]
    if len(set(missing_sets)) > 1:
        return (
            "เขตที่ถูกจัดอันดับขาดองค์ประกอบไม่เหมือนกัน "
            "คะแนนจึงใช้เทียบได้ภายในกลุ่มที่ข้อมูลชุดเดียวกันเท่านั้น "
            "ส่วนที่ไม่มีข้อมูลได้ศูนย์เพราะไม่มีตัวเลข ไม่ได้แปลว่าปัจจัยนั้นแย่ และไม่ได้ทำให้เขตดูดีขึ้น"
        )
    missing = missing_sets[0]
    if not missing:
        return None
    return (
        "ทุกเขตที่จัดอันดับยังขาด " + ", ".join(missing) + " "
        "คะแนนส่วนที่ขาดเป็นศูนย์เพราะไม่มีข้อมูล ไม่ได้แปลว่าปัจจัยนั้นแย่ และไม่ได้ทำให้เขตดูดีขึ้น"
    )


def _announcement_component(area: AreaInput) -> tuple[float | None, str]:
    status = area.coverage_status
    if status == "เปิดรับสมัคร":
        return 100.0, "มีช่วงรับสมัครที่ยืนยันและวันนี้ตกอยู่ในช่วงนั้น"
    if status == "ประกาศแล้ว":
        return 70.0, "มีตำแหน่งที่ประกาศแล้ว แต่ยังไม่มีหลักว่าวันนี้เปิดรับสมัคร และยังไม่มีอายุบัญชีของรอบนี้"
    if status == "รอประกาศ":
        return 20.0, "อยู่ในรายชื่อรอประกาศ ยังไม่ใช่ตำแหน่งสอบที่ยืนยัน"
    if status in {"ไม่มีข้อมูล", "ตรวจสอบไม่ได้", "หน้าต้นทางระบุว่าไม่เปิดสอบ"}:
        return None, f"สถานะประกาศคือ {status}"
    return None, "ยังไม่มีสถานะประกาศที่ยืนยัน"
