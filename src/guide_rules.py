"""Checklist for the 2569 general English assistant-teacher choice.

The member guide states a sequence of checks. It does not state a formula or
weights, so this module does not invent a replacement score and does not change
the existing interest score. Page numbers refer to that guide. Wording here is
paraphrased.
"""

from __future__ import annotations

import re

from src.connectors.gazette import norm_area
from src.labels import MISSING, NOT_FOUND
from src.scoring import AreaInput

# Guide page 3: the 2569 list is expected to last about two years, announced late
# November 2569 and used through late November 2571, covering fiscal years 2569-2571.
ACCOUNT_WINDOW = "บัญชีปี 2569 มีอายุประมาณ 2 ปี ใช้บริหารเกษียณปีงบ 2569-2571"
# Guide pages 3 and 8: do not apply on the first days; watch applicants and apply 23-25 Oct 2569.
APPLY_WINDOW = "ช่วงสมัครที่คู่มือแนะนำคือ 23-25 ตุลาคม 2569 หลังจากเห็นจำนวนผู้สมัครแล้ว"
# Guide page 9: English is one of the five core primary subjects.
PRIMARY_CORE = ("ปฐมวัย", "ไทย", "คณิต", "อังกฤษ")

UNCLEAR = (
    {
        "id": "weights",
        "pages": "3-9",
        "status": "ต้องตรวจสอบ",
        "note": "คู่มือไม่ได้กำหนดสูตรหรือน้ำหนักรวม จึงไม่สร้างคะแนนใหม่แทนสูตรความน่าสนใจเดิม",
    },
    {
        "id": "transfers",
        "pages": "7",
        "status": "ต้องตรวจสอบ",
        "note": "คู่มือบอกว่ารอบทั่วไปและการรับย้ายใช้โควตาเดียวกัน แต่ไม่มีจำนวนคนย้ายเข้ารายเขตในไฟล์ที่อ่านได้",
    },
    {
        "id": "secondary_english",
        "pages": "3, 9",
        "status": "ต้องตรวจสอบ",
        "note": "คู่มือผูก 5 เอกหลักกับมาตรฐานประถม ไม่ได้บอกว่าเอกอังกฤษระดับมัธยมได้ลำดับเดียวกัน",
    },
    {
        "id": "own_rank",
        "pages": "9",
        "status": "ต้องตรวจสอบ",
        "note": "คู่มือพูดถึงลำดับ 1-10 ของผู้สมัคร แอปไม่มีลำดับคะแนนของผู้ใช้ จึงไม่แปลข้อนั้นเป็นโอกาสบรรจุ",
    },
    {
        "id": "retirement_rows",
        "pages": "3, 6",
        "status": "ต้องตรวจสอบ",
        "note": "ไฟล์เกษียณเป็นยอดทุกวิชา และชื่อเขตในบางช่องซ้อนกัน จึงไม่ใส่ยอดนั้นเป็นตำแหน่งเอกอังกฤษ",
    },
)


def province_of(area_name: str) -> str:
    text = re.sub(r"^สพ[ปม]\.\s*", "", area_name or "")
    return re.sub(r"\s*เขต\s*\d+.*$", "", text).strip()


def zone_of(area_name: str) -> int | None:
    found = re.search(r"เขต\s*(\d+)", area_name or "")
    return int(found.group(1)) if found else None


def multi_zone_provinces(areas: list[AreaInput]) -> set[str]:
    counts: dict[str, set[int]] = {}
    for area in areas:
        if not area.area_name.startswith("สพป."):
            continue
        zone = zone_of(area.area_name)
        if zone is None:
            continue
        counts.setdefault(province_of(area.area_name), set()).add(zone)
    return {name for name, zones in counts.items() if len(zones) >= 2}


def _history_points(area: AreaInput) -> list[dict]:
    points = []
    for year in (2566, 2567, 2568):
        stat = area.years.get(year)
        if stat is None or stat.listed_count is None or stat.appointed_count is None:
            continue
        if stat.listed_count <= 0 or not 0 <= stat.appointed_count <= stat.listed_count:
            continue
        points.append(
            {
                "year": year,
                "listed": stat.listed_count,
                "appointed": stat.appointed_count,
                "remaining": stat.remaining_count,
                "source": "recruitment",
            }
        )
    return points


def _account_points(area_key: str, events: list[dict], area_name: str = "") -> list[dict]:
    points = []
    wanted = norm_area(area_name) if area_name else ""
    for event in events:
        same_key = event.get("area_key") == area_key
        same_name = bool(wanted) and norm_area(event.get("area_name") or "") == wanted
        if not same_key and not same_name:
            continue
        listed = _listed_from_evidence(event.get("evidence") or "")
        appointed = event.get("appointed_count")
        remaining = event.get("remaining_count")
        if listed is None or appointed is None or remaining is None:
            continue
        if listed - appointed != remaining:
            continue
        points.append(
            {
                "year": event["exam_year"],
                "listed": listed,
                "appointed": appointed,
                "remaining": remaining,
                "source": event.get("source_url") or "",
                "announced_at": event.get("announced_at"),
                "observed_at": event.get("observed_at") or "",
            }
        )
    points.sort(key=lambda item: (item["year"], item["source"]))
    return points


def _listed_from_evidence(evidence: str) -> int | None:
    found = re.search(r"listed\s+(\d+)", evidence or "")
    return int(found.group(1)) if found else None


def _call_ratio(history: list[dict], accounts: list[dict]) -> float | None:
    """Recruitment history stays first. A balance row is used only when that history is absent."""
    source = history[-1] if history else (accounts[-1] if accounts else None)
    if source is None or not source["listed"]:
        return None
    return source["appointed"] / source["listed"]


def _trend_note(points: list[dict]) -> str:
    usable = [item for item in points if item["source"] == "recruitment" or item["remaining"] is not None]
    years = sorted({item["year"] for item in usable})
    if len(years) < 2:
        return "มีสถิติเรียกบรรจุไม่ถึง 2 ปี จึงยังไม่สรุปแนวโน้ม"
    return "มีมากกว่า 1 ปี แต่คนละแหล่งอาจขัดกัน ดูทีละปี ไม่ได้สรุปเป็นอัตราเพิ่มหรือลด"


def explain_area(area: AreaInput, focus_year: int, account_events: list[dict], risky_provinces: set[str]) -> dict:
    """One district against the guide checklist. Missing factors stay uncertain."""
    stat = area.years.get(focus_year)
    openings = stat.openings if stat and stat.opened and stat.openings else None
    applicants_known = bool(stat and stat.applicants_known and stat.applicants is not None)
    history = _history_points(area)
    accounts = _account_points(area.area_key, account_events)
    factors = []

    if openings:
        factors.append(
            {
                "factor": "อัตราว่างที่เปิดสอบ",
                "kind": "ข้อเท็จจริง",
                "pages": "3-4",
                "text": (
                    f"ปี {focus_year} เปิดรับเอกภาษาอังกฤษ {openings} ตำแหน่ง "
                    "เป็นอัตราว่างปัจจุบันตามประกาศ ไม่ใช่อัตราเกษียณ และไม่ใช่ที่ว่างจากการย้ายในอนาคต"
                ),
                "source": area.source_url or "",
            }
        )
    else:
        factors.append(
            {
                "factor": "อัตราว่างที่เปิดสอบ",
                "kind": "ยังไม่มี",
                "pages": "3-4",
                "text": f"ปี {focus_year} ยังไม่มีจำนวนตำแหน่งเอกภาษาอังกฤษที่ยืนยัน",
                "source": "",
            }
        )

    if applicants_known and openings:
        factors.append(
            {
                "factor": "จำนวนผู้สมัคร",
                "kind": "ข้อเท็จจริง",
                "pages": "3, 8",
                "text": f"ผู้สมัคร {stat.applicants} คน ต่อ {openings} ตำแหน่ง",
                "source": area.source_url or "",
            }
        )
    else:
        factors.append(
            {
                "factor": "จำนวนผู้สมัคร",
                "kind": "รอประกาศ",
                "pages": "3, 8",
                "text": f"ยัง{NOT_FOUND if focus_year == 2569 else MISSING}จำนวนผู้สมัคร คู่มือให้รอดูตัวเลขนี้ก่อนสมัคร {APPLY_WINDOW}",
                "source": area.area_website or "",
            }
        )

    if history:
        shown = " ".join(f"ปี {item['year']} เรียก {item['appointed']} จากขึ้นบัญชี {item['listed']}" for item in history)
        factors.append(
            {
                "factor": "สถิติเรียกบรรจุ 2566-2568",
                "kind": "ข้อเท็จจริง",
                "pages": "3, 7",
                "text": shown + " ตัวเลขนี้ไม่ใช่ลำดับบัญชีที่เรียกถึง และไม่ได้การันตีรอบ 2569",
                "source": area.source_url or "",
            }
        )
    else:
        factors.append(
            {
                "factor": "สถิติเรียกบรรจุ 2566-2568",
                "kind": "ยังไม่มี",
                "pages": "3, 7",
                "text": "ยังไม่มีทั้งจำนวนขึ้นบัญชีและจำนวนเรียกบรรจุของเอกนี้ในช่วง 2566-2568 จากหน้ารวบรวม",
                "source": "",
            }
        )

    if accounts:
        shown = " ".join(
            f"ปี {item['year']} ขึ้นบัญชี {item['listed']} เรียก {item['appointed']} คงเหลือ {item['remaining']}"
            for item in accounts
        )
        factors.append(
            {
                "factor": "บัญชีคงเหลือเอกภาษาอังกฤษ",
                "kind": "ข้อเท็จจริง",
                "pages": "7",
                "text": shown + " เป็นบัญชีคงเหลือของปีนั้น ไม่ใช่ตำแหน่งเปิดสอบปี 2569",
                "source": accounts[0]["source"],
            }
        )
    else:
        factors.append(
            {
                "factor": "บัญชีคงเหลือเอกภาษาอังกฤษ",
                "kind": "ยังไม่มี",
                "pages": "7",
                "text": "ยังไม่มีแถวเอกภาษาอังกฤษที่ขึ้นบัญชี เรียกบรรจุ และคงเหลือลงกันได้",
                "source": "",
            }
        )

    factors.append(
        {
            "factor": "แนวโน้มการเรียก",
            "kind": "ยังสรุปไม่ได้" if len({item["year"] for item in history + accounts}) < 2 else "ข้อเท็จจริงรายปี",
            "pages": "7",
            "text": _trend_note(history + accounts),
            "source": "",
        }
    )
    factors.append(
        {
            "factor": "อัตราเกษียณ 2569-2571",
            "kind": "ต้องตรวจสอบ",
            "pages": "3, 6",
            "text": (
                "คู่มือให้ดูเกษียณเป็นอัตราว่างของรอบถัดไป ไม่ใช่ตำแหน่งที่เปิดสอบตอนนี้ "
                "ไฟล์ที่แนบเป็นยอดทุกวิชาและจัดแถวรายเขตไม่ปลอดภัย จึงไม่นำมาเป็นตำแหน่งเอกอังกฤษ"
            ),
            "source": "",
        }
    )

    sp_primary = area.area_name.startswith("สพป.")
    factors.append(
        {
            "factor": "มาตรฐานวิชาเอก",
            "kind": "ข้ออนุมานจากคู่มือ" if sp_primary else "ต้องตรวจสอบ",
            "pages": "3, 9",
            "text": (
                "เอกอังกฤษเป็น 1 ใน 5 เอกหลักของมาตรฐานประถม ตามที่คู่มือระบุ"
                if sp_primary
                else "เขตนี้เป็นระดับมัธยม คู่มือไม่ได้ยืนยันว่าเอกอังกฤษได้ลำดับเดียวกับ 5 เอกหลักของประถม"
            ),
            "source": "",
        }
    )

    zone_risk = sp_primary and zone_of(area.area_name) == 1 and province_of(area.area_name) in risky_provinces
    factors.append(
        {
            "factor": "ความเสี่ยงรับย้าย",
            "kind": "ข้ออนุมานจากคู่มือ" if zone_risk else "ต้องตรวจสอบ",
            "pages": "7",
            "text": (
                "เป็นเขต 1 ของจังหวัดที่มีหลายเขต คู่มือเตือนว่าเขตแบบนี้มักถูกกันอัตราไว้รับย้าย แต่แอปไม่มีจำนวนคนย้ายเข้า"
                if zone_risk
                else "ยังไม่มีจำนวนคนย้ายเข้าหรือย้ายออกของเขตนี้"
            ),
            "source": "",
        }
    )
    if area.staffing_actual is not None and area.staffing_standard not in (None, 0):
        gap = area.staffing_actual - area.staffing_standard
        factors.append(
            {
                "factor": "อัตรากำลัง",
                "kind": "ข้อเท็จจริงที่คู่มือไม่ได้ใช้เป็นเกณฑ์",
                "pages": "-",
                "text": f"ครูจริง {area.staffing_actual} เกณฑ์ {area.staffing_standard} ผลต่าง {gap:+d} การขาดครูไม่ใช่ตำแหน่งเปิดสอบและไม่ใช่อัตราเกษียณ",
                "source": area.staffing_source_url or area.detail_url or "",
            }
        )

    pros = [item["text"] for item in factors if item["kind"] == "ข้อเท็จจริง" and item["factor"] in {"อัตราว่างที่เปิดสอบ", "สถิติเรียกบรรจุ 2566-2568"}]
    cons = [item["text"] for item in factors if item["kind"] in {"รอประกาศ", "ยังไม่มี", "ต้องตรวจสอบ"}]
    risks = [item["text"] for item in factors if item["factor"] == "ความเสี่ยงรับย้าย"]
    if not applicants_known:
        risks.append("ยังไม่ควรล็อกเขต เพราะยังไม่เห็นจำนวนผู้สมัคร")
    return {
        "area_key": area.area_key,
        "area_name": area.area_name,
        "org_level": area.org_level,
        "openings": openings,
        "locked": False,
        "confidence": "รอจำนวนผู้สมัคร" if not applicants_known else "มีผู้สมัครแล้ว",
        "call_ratio": _call_ratio(history, accounts),
        "zone_risk": zone_risk,
        "primary_core": sp_primary,
        "factors": factors,
        "pros": pros,
        "cons": cons,
        "risks": risks,
    }


def guide_watch(areas: list[AreaInput], all_areas: list[AreaInput], focus_year: int, account_events: list[dict]) -> dict:
    """Follow-up list. Not a pass chance and not a weighted score."""
    risky = multi_zone_provinces(all_areas or areas)
    explained = []
    for area in areas:
        stat = area.years.get(focus_year)
        if not (stat and stat.opened and stat.openings):
            continue
        explained.append(explain_area(area, focus_year, account_events, risky))

    def sort_key(item: dict) -> tuple:
        ratio = item["call_ratio"]
        return (item["call_ratio"] is None, -(ratio or 0), -(item["openings"] or 0), item["area_name"])

    primary = [item for item in explained if item["primary_core"] and item["call_ratio"] is not None and not item["zone_risk"]]
    backup = [item for item in explained if item["primary_core"] and item["call_ratio"] is not None and item["zone_risk"]]
    hold = [item for item in explained if item not in primary and item not in backup]
    primary.sort(key=sort_key)
    backup.sort(key=sort_key)
    hold.sort(key=sort_key)
    return {
        "locked": False,
        "headline": (
            f"ยังไม่ล็อกเขตสอบปี {focus_year} {APPLY_WINDOW} "
            "รายการด้านล่างเป็นเขตที่เปิดรับจริงและมีประวัติเรียกบรรจุให้ตรวจต่อ ไม่ใช่โอกาสสอบติดหรือโอกาสถูกบรรจุ"
        ),
        "account_window": ACCOUNT_WINDOW,
        "order_note": (
            "เรียงจากสัดส่วนที่เคยเรียกบรรจุจากผู้ขึ้นบัญชีในปีที่มีข้อมูล "
            "คู่มือไม่ได้กำหนดน้ำหนัก และบอกไว้ว่าเขตที่เปิดน้อยไม่ได้แปลว่ารอบหน้าจะเรียกน้อย "
            "คะแนนความน่าสนใจเดิมยังใช้สูตรเดิม เพราะบัญชีคงเหลือไม่ถูกนำไปคิดในสูตรสอบทั่วไป"
        ),
        "unclear": UNCLEAR,
        "primary": primary,
        "backup": backup,
        "hold": hold,
    }


RATIO_NOTE = (
    "แต่ละสัดส่วนคือเรียกบรรจุหารด้วยขึ้นบัญชีของปีเดียวและแหล่งเดียว "
    "ปี 2566, 2567 และ 2568 ไม่ถูกนำมารวมกัน และหน้ารวบรวมไม่ถูกนำมารวมกับบัญชีคงเหลือ "
    "คะแนนความน่าสนใจเดิมอยู่คนละตารางและไม่ได้ถูกคำนวณใหม่ "
    "คะแนนและสัดส่วนเหล่านี้ไม่ใช่โอกาสสอบติดหรือโอกาสถูกบรรจุ"
)
COMPARABLE = "หน้ารวบรวมรอบสอบทั่วไป ปี 2567: ตัวตั้งคือเรียกบรรจุ ตัวหารคือขึ้นบัญชีของปีเดียวกัน"
NO_DOCUMENT_DATE = "ไม่พบวันที่"


def _audit_history(area: AreaInput) -> list[dict]:
    """Read stored recruitment figures, including rows whose arithmetic does not hold."""
    points = []
    for year in (2566, 2567, 2568):
        stat = area.years.get(year)
        if stat is None:
            continue
        if stat.listed_count is None and stat.appointed_count is None and stat.remaining_count is None:
            continue
        points.append(
            {
                "year": year,
                "listed": stat.listed_count,
                "appointed": stat.appointed_count,
                "remaining": stat.remaining_count,
                "source": area.source_url or "",
                "kind": "หน้ารวบรวม",
                "announced_at": area.reference_at,
            }
        )
    return points


def _audit_accounts(area: AreaInput, events: list[dict]) -> list[dict]:
    """Read stored balance figures. A missing listed count stays missing."""
    wanted = norm_area(area.area_name)
    points = []
    for event in events:
        same_key = event.get("area_key") == area.area_key
        same_name = bool(wanted) and norm_area(event.get("area_name") or "") == wanted
        if not same_key and not same_name:
            continue
        if event.get("exam_year") not in (2566, 2567, 2568):
            continue
        appointed = event.get("appointed_count")
        remaining = event.get("remaining_count")
        listed = _listed_from_evidence(event.get("evidence") or "")
        if listed is None and appointed is None and remaining is None:
            continue
        points.append(
            {
                "year": event["exam_year"],
                "listed": listed,
                "appointed": appointed,
                "remaining": remaining,
                "source": event.get("source_url") or "",
                "kind": "บัญชีคงเหลือ",
                "announced_at": event.get("announced_at"),
            }
        )
    points.sort(key=lambda item: (item["year"], item["source"]))
    return points


def _equation_notes(points: list[dict]) -> tuple[list[str], set[tuple]]:
    """Flag rows that break listed = appointed + remaining. Stored numbers are not changed."""
    conflicts = []
    blocked = set()
    for item in points:
        listed, appointed, remaining = item["listed"], item["appointed"], item["remaining"]
        if listed is None or appointed is None or remaining is None:
            continue
        if listed != appointed + remaining:
            conflicts.append(
                f"ปี {item['year']} {item['kind']} ขึ้นบัญชี {listed} ไม่เท่ากับเรียกบรรจุ {appointed} + คงเหลือ {remaining}"
            )
            blocked.add((item["year"], item["kind"]))
    return conflicts, blocked


def _shares(points: list[dict], blocked: set[tuple]) -> list[dict]:
    """One share per year and source. A broken row is not converted into a share."""
    shares = []
    for item in points:
        if (item["year"], item["kind"]) in blocked:
            continue
        listed, appointed = item["listed"], item["appointed"]
        if listed is None or appointed is None or listed <= 0:
            continue
        shares.append(
            {
                "year": item["year"],
                "kind": item["kind"],
                "numerator": appointed,
                "denominator": listed,
                "source": item["source"],
            }
        )
    return shares


def _appointed_conflicts(history: list[dict], accounts: list[dict]) -> list[str]:
    notes = []
    for account in accounts:
        recruit = next((item for item in history if item["year"] == account["year"]), None)
        if recruit and recruit["appointed"] is not None and account.get("appointed") is not None and recruit["appointed"] != account["appointed"]:
            notes.append(
                f"ปี {account['year']} หน้ารวบรวมเรียกบรรจุ {recruit['appointed']} "
                f"แต่บัญชีคงเหลือระบุ {account['appointed']}"
            )
    return notes


def _year_text(points: list[dict]) -> str:
    if not points:
        return MISSING
    return " ; ".join(
        f"ปี {item['year']}: ขึ้นบัญชี {item['listed']} เรียกบรรจุ {item['appointed']} คงเหลือ {item['remaining'] if item['remaining'] is not None else MISSING}"
        for item in points
    )


def _cell(points: list[dict], year: int) -> str:
    found = [item for item in points if item["year"] == year]
    if not found:
        return MISSING
    chunks = []
    for item in found:
        listed = item["listed"] if item["listed"] is not None else "ไม่พบในข้อความหลักฐาน"
        appointed = item["appointed"] if item["appointed"] is not None else MISSING
        remaining = item["remaining"] if item["remaining"] is not None else MISSING
        chunks.append(f"ขึ้นบัญชี {listed} เรียกบรรจุ {appointed} คงเหลือ {remaining}")
    return " ; ".join(chunks)


def _share_text(shares: list[dict]) -> str:
    if not shares:
        return "ไม่คำนวณสัดส่วน"
    return " ; ".join(
        f"ปี {item['year']} {item['kind']} {item['numerator']}/{item['denominator']}"
        for item in shares
    )


def _document_date(points: list[dict]) -> str:
    dates = [point["announced_at"] for point in points if point.get("announced_at")]
    if not dates:
        return NO_DOCUMENT_DATE
    return " ; ".join(dict.fromkeys(dates))


def evidence_review(areas: list[AreaInput], all_areas: list[AreaInput], focus_year: int, account_events: list[dict]) -> dict:
    """Evidence table for opened districts. Does not calculate or change the interest score."""
    risky = multi_zone_provinces(all_areas or areas)
    rows = []
    for area in all_areas or areas:
        stat = area.years.get(focus_year)
        if not (stat and stat.opened and stat.openings):
            continue
        history = _audit_history(area)
        accounts = _audit_accounts(area, account_events)
        history_conflicts, history_blocked = _equation_notes(history)
        account_conflicts, account_blocked = _equation_notes(accounts)
        conflicts = history_conflicts + account_conflicts + _appointed_conflicts(history, accounts)
        shares = _shares(history, history_blocked) + _shares(accounts, account_blocked)
        applicants_known = bool(stat.applicants_known and stat.applicants is not None)
        recruit_2567 = next(
            (
                item for item in shares
                if item["kind"] == "หน้ารวบรวม" and item["year"] == 2567
            ),
            None,
        )
        clean_2567 = recruit_2567 is not None and not conflicts
        if applicants_known and clean_2567:
            group = "sufficient"
            reliability = "หลักฐานเพียงพอ"
        elif clean_2567:
            group = "incomplete"
            reliability = "น่าสนใจแต่ข้อมูลยังไม่ครบ"
        else:
            group = "wait"
            reliability = "ต้องรอข้อมูลหรือมีข้อมูลขัดแย้ง"
        zone_risk = area.area_name.startswith("สพป.") and zone_of(area.area_name) == 1 and province_of(area.area_name) in risky
        primary_share = next((item for item in shares if item["kind"] == "หน้ารวบรวม" and item["year"] == 2567), None)
        if primary_share is None and len(shares) == 1:
            primary_share = shares[0]
        missing = []
        if not applicants_known:
            missing.append(f"จำนวนผู้สมัครปี {focus_year}")
        for year in (2566, 2567, 2568):
            if not any(item["year"] == year for item in history):
                missing.append(f"หน้ารวบรวมปี {year}")
        if not accounts:
            missing.append("บัญชีคงเหลือที่จับคู่ชื่อได้")
        if any(item["listed"] is None or item["appointed"] is None or item["remaining"] is None for item in history + accounts):
            missing.append("ตัวเลขขึ้นบัญชี เรียกบรรจุ หรือคงเหลือบางช่อง")
        if not any(item.get("announced_at") for item in accounts):
            missing.append("วันที่ในเอกสารบัญชี")
        rows.append(
            {
                "area_key": area.area_key,
                "area_name": area.area_name,
                "group": group,
                "reliability": reliability,
                "openings": stat.openings,
                "applicants": stat.applicants if applicants_known else "รอข้อมูล",
                "recruitment_by_year": {year: _cell(history, year) for year in (2566, 2567, 2568)},
                "balance_by_year": {year: _cell(accounts, year) for year in (2566, 2567, 2568)},
                "shares": shares,
                "share_text": _share_text(shares),
                "numerator": None if primary_share is None else primary_share["numerator"],
                "denominator": None if primary_share is None else primary_share["denominator"],
                "basis_year": None if primary_share is None else primary_share["year"],
                "basis_definition": None if primary_share is None else primary_share["kind"],
                "comparable": bool(primary_share and primary_share["kind"] == "หน้ารวบรวม" and primary_share["year"] == 2567 and not conflicts),
                "recruitment_date": area.reference_at or NO_DOCUMENT_DATE,
                "balance_announced": _document_date(accounts),
                "recruitment_source": area.source_url or "",
                "balance_source": " ; ".join(dict.fromkeys(item["source"] for item in accounts if item["source"])),
                "missing": missing,
                "conflicts": conflicts,
                "zone_caution": "เขต 1 ของจังหวัดที่มีหลายเขตในตารางสอบ" if zone_risk else "",
                "staffing_actual": area.staffing_actual,
                "staffing_standard": area.staffing_standard,
            }
        )
    rows.sort(key=lambda row: ({"sufficient": 0, "incomplete": 1, "wait": 2}[row["group"]], row["area_name"]))
    grouped = {"sufficient": [], "incomplete": [], "wait": []}
    for row in rows:
        grouped[row["group"]].append(row)
    watched = []
    for row in grouped["incomplete"]:
        share = next((item for item in row["shares"] if item["year"] == 2567 and item["kind"] == "หน้ารวบรวม"), None)
        facts = [f"ปี {focus_year} เปิดเอกภาษาอังกฤษ {row['openings']} อัตรา"]
        if share:
            facts.append(
                f"ปี 2567 หน้ารวบรวมเรียกบรรจุ {share['numerator']} จากขึ้นบัญชี {share['denominator']} "
                "และสมการขึ้นบัญชีเท่ากับเรียกบรรจุบวกคงเหลือลงกันในปีนี้"
            )
        facts.append("จำนวนผู้สมัครปี 2569 ยังเป็นรอข้อมูล")
        assumptions = ["สัดส่วนของปี 2567 ไม่ได้บอกผลการเรียกบรรจุปี 2569"]
        if row["zone_caution"]:
            assumptions.append("คู่มือสันนิษฐานว่าเขต 1 ของจังหวัดหลายเขตอาจถูกกันอัตรารับย้าย แต่ไม่มีจำนวนคนย้ายเข้า")
        else:
            assumptions.append("ยังไม่มีจำนวนคนย้ายเข้า จึงยังไม่สรุปผลของการรับย้าย")
        if row["area_name"].startswith("สพม."):
            assumptions.append("คู่มือไม่ได้ยืนยันว่าเอกอังกฤษระดับมัธยมใช้เกณฑ์เดียวกับประถม")
        watched.append({"area_name": row["area_name"], "facts": facts, "assumptions": assumptions, "source": row["recruitment_source"]})
    watched.sort(key=lambda item: item["area_name"])
    decision = _consideration(rows, focus_year)
    return {
        "ratio_note": RATIO_NOTE,
        "comparable_definition": COMPARABLE,
        "rows": rows,
        "sufficient": grouped["sufficient"],
        "incomplete": grouped["incomplete"],
        "wait": grouped["wait"],
        "watch": watched,
        "decision": decision,
        "interest_score_unchanged": True,
    }


def _balance_support(row: dict) -> list[dict]:
    return [item for item in row["shares"] if item["kind"] == "บัญชีคงเหลือ" and item["denominator"] > 0]


def _consideration_facts(row: dict, focus_year: int) -> str:
    parts = [
        f"ปี {focus_year} เปิดเอกภาษาอังกฤษ {row['openings']} อัตรา และจำนวนผู้สมัครยังเป็นรอข้อมูล",
        f"หน้ารวบรวมปี 2567: {row['recruitment_by_year'][2567]}",
    ]
    for year in (2566, 2567, 2568):
        text = row["balance_by_year"][year]
        if text != MISSING:
            parts.append(f"บัญชีคงเหลือปี {year}: {text} เป็นคนละแหล่ง จึงไม่นำมารวมสัดส่วนกับหน้ารวบรวม")
    parts.append(f"วันที่บนหน้ารวบรวม {row['recruitment_date']} วันที่ในเอกสารบัญชี {row['balance_announced']}")
    parts.append("แหล่งหน้ารวบรวม " + (row["recruitment_source"] or MISSING))
    if row["balance_source"]:
        parts.append("แหล่งบัญชี " + row["balance_source"])
    if row["conflicts"]:
        parts.append("ข้อขัดแย้ง: " + " ; ".join(row["conflicts"]))
    else:
        parts.append("ไม่พบข้อขัดแย้งของจำนวนเรียกบรรจุในปีเดียวกัน")
    parts.append("ข้อมูลที่ขาด: " + " ; ".join(row["missing"]))
    return " ".join(parts)


def _recruit_2567(row: dict) -> dict | None:
    return next((item for item in row["shares"] if item["kind"] == "หน้ารวบรวม" and item["year"] == 2567), None)


def _same_year_agreement(row: dict, recruit: dict | None) -> bool:
    if recruit is None:
        return False
    return any(
        item["kind"] == "บัญชีคงเหลือ"
        and item["year"] == recruit["year"]
        and item["numerator"] == recruit["numerator"]
        and item["denominator"] == recruit["denominator"]
        for item in row["shares"]
    )


def _called_less_than_half(recruit: dict | None) -> bool:
    if recruit is None or not recruit["denominator"]:
        return False
    return recruit["numerator"] * 2 < recruit["denominator"]


def _is_zone_one(row: dict) -> bool:
    return zone_of(row["area_name"]) == 1 or bool(row["zone_caution"])


def _choice_label(row: dict, focus_year: int) -> dict:
    """Label every opened district. A district is never removed without a reason."""
    recruit = _recruit_2567(row)
    low_call = _called_less_than_half(recruit)
    zone_one = _is_zone_one(row)
    same_year = _same_year_agreement(row, recruit)
    other_balance = bool(_balance_support(row)) and not same_year
    call_text = "ยังไม่มีสัดส่วนหน้ารวบรวมปี 2567"
    if recruit:
        call_text = f"ปี 2567 เรียกบรรจุ {recruit['numerator']} จากขึ้นบัญชี {recruit['denominator']}"
    assumptions = ["สัดส่วนปี 2567 ไม่ได้บอกผลการเรียกบรรจุปี 2569 และไม่ใช่โอกาสบรรจุ"]
    if zone_one:
        if row["zone_caution"]:
            assumptions.append("คู่มือสันนิษฐานว่าเขต 1 ของจังหวัดหลายเขตอาจถูกกันอัตรารับย้าย แต่ไม่มีจำนวนคนย้ายเข้า")
        else:
            assumptions.append("ชื่อเป็นเขต 1 แต่ตารางที่โหลดมายังไม่เห็นเขตอื่นของจังหวัดเดียวกัน จึงยังยืนยันจำนวนรับย้ายไม่ได้")
    else:
        assumptions.append("ยังไม่มีจำนวนคนย้ายเข้า")
    if row["area_name"].startswith("สพม."):
        assumptions.append("คู่มือไม่ได้ยืนยันว่าเอกอังกฤษระดับมัธยมใช้เกณฑ์เดียวกับประถม")
    if row["staffing_actual"] is None or row["staffing_standard"] is None:
        assumptions.append("อัตรากำลังที่ไม่มีตัวเลขไม่ได้ถูกนับเป็นศูนย์")
    facts = _consideration_facts(row, focus_year)
    if row["staffing_actual"] is not None and row["staffing_standard"] is not None:
        gap = row["staffing_actual"] - row["staffing_standard"]
        facts += f" อัตรากำลังเอกอังกฤษ {row['staffing_actual']} จากเกณฑ์ {row['staffing_standard']} ผลต่าง {gap:+d} เป็นบริบท ไม่ใช่ตำแหน่งสอบ"
    if row["conflicts"]:
        status, order = "มีข้อมูลขัดแย้ง", 7
        reason = "ไม่ใช้เป็นตัวเลือกหลักเพราะ " + " ; ".join(row["conflicts"]) + " ตัวเลขต้นทางไม่ได้ถูกเขียนทับ"
    elif recruit is None:
        status, order = "ยังไม่มีประวัติเรียกบรรจุปี 2567 ที่ใช้ได้", 6
        reason = "ไม่ใช้เป็นตัวเลือกหลักเพราะยังไม่มีหน้ารวบรวมปี 2567 ที่สมการลงกันและขึ้นบัญชีมากกว่า 0"
    elif zone_one:
        status, order = "ไม่เน้นเพราะเป็นเขต 1", 5
        reason = f"ไม่เน้นเขตนี้เพราะเป็นเขต 1 {call_text} ข้อควรระวังรับย้ายยังไม่มีจำนวนคนย้ายเข้า"
        if low_call:
            reason += " และเรียกบรรจุไม่ถึงครึ่งของบัญชีปี 2567"
    elif row["area_name"].startswith("สพม."):
        status, order = "มัธยม ยังไม่ใช้เป็นตัวเลือกหลัก", 4
        reason = f"ไม่ใช้เป็นตัวเลือกหลักเพราะคู่มือไม่ได้ยืนยันเกณฑ์มัธยม {call_text}"
    elif low_call:
        status, order = "เรียกบรรจุน้อย", 3
        reason = (
            f"ลดความน่าสนใจเพราะ{call_text} ซึ่งน้อยกว่าครึ่งของบัญชีปีนั้น "
            "เกณฑ์ครึ่งหนึ่งเป็นกติกาตรวจของรอบนี้ ไม่ใช่สูตรในคู่มือ และไม่ได้ลบเขตออก"
        )
    elif same_year:
        status, order = "หลักฐานปี 2567 ตรงกัน", 0
        reason = (
            f"หน้ารวบรวมและบัญชีคงเหลือปี 2567 ระบุตัวเลขชุดเดียวกัน {call_text} "
            f"จำนวนอัตราที่เปิดปี {focus_year} ไม่ได้ถูกใช้เป็นเหตุผลที่เลือก"
        )
    elif other_balance:
        status, order = "เรียกบรรจุไม่น้อย แต่บัญชีคนละปี", 1
        reason = (
            f"{call_text} และมีบัญชีคงเหลืออีกปีแยกไว้ คนละปีไม่ได้ถูกรวมเป็นสัดส่วนเดียว "
            "จึงยังไม่ขึ้นเป็นตัวเลือกหลักเหนือเขตที่แหล่งปีเดียวกันตรงกัน"
        )
    else:
        status, order = "มีเฉพาะหน้ารวบรวมปี 2567", 2
        reason = (
            f"{call_text} แต่ยังไม่มีบัญชีคงเหลือที่ขึ้นบัญชีมากกว่า 0 มายืนยัน "
            f"การเปิด {row['openings']} อัตราไม่ได้ทำให้เขตนี้ขึ้นเป็นตัวเลือกหลัก"
        )
    return {
        "status": status,
        "order": order,
        "reason": reason,
        "facts": facts,
        "assumptions": assumptions,
        "same_year": same_year and not zone_one and not row["conflicts"] and recruit is not None and not low_call and row["area_name"].startswith("สพป."),
    }


def _consideration(rows: list[dict], focus_year: int) -> dict:
    """One-district review. Every opened district stays visible with its own reason."""
    labeled = []
    for row in rows:
        labeled.append(row | _choice_label(row, focus_year))
    primary = [item for item in labeled if item["same_year"]]
    primary.sort(key=lambda item: item["area_name"])
    for item in labeled:
        if item in primary and len(primary) == 1:
            item["status"] = "ตัวเลือกหลักเบื้องต้น"
        elif item in primary:
            item["status"] = "หลักฐานปีเดียวกันเสมอกัน"
            item["reason"] += " มีมากกว่า 1 เขตที่แหล่งปีเดียวกันตรงกัน จึงไม่ใช้จำนวนอัตราที่เปิดมาตัดสินให้เหลือเขตเดียว"
    labeled.sort(key=lambda item: (0 if item["status"] == "ตัวเลือกหลักเบื้องต้น" else item["order"], item["area_name"]))
    if len(primary) == 1:
        note = (
            f"ตัวเลือกหลักเบื้องต้นคือ {primary[0]['area_name']} "
            "เพราะหน้ารวบรวมกับบัญชีคงเหลือปี 2567 ตรงกัน และไม่ได้เลือกเพราะจำนวนอัตราที่เปิด "
            "อันดับนี้ไม่ใช่โอกาสสอบติดหรือโอกาสบรรจุ และต้องเปลี่ยนเมื่อมีจำนวนผู้สมัครปี 2569"
        )
    elif len(primary) > 1:
        note = (
            "มีมากกว่า 1 เขตที่หลักฐานปี 2567 ตรงกัน จึงยังไม่ยุบเหลือเขตเดียวด้วยจำนวนอัตราที่เปิด "
            "อันดับนี้ไม่ใช่โอกาสสอบติด"
        )
    else:
        note = "ยังไม่มีเขตประถมที่หน้ารวบรวมกับบัญชีคงเหลือปี 2567 ตรงกัน จึงยังไม่ล็อกเขตเดียว"
    return {
        "primary": primary,
        "rows": labeled,
        "targets": [item for item in labeled if item["status"] == "ตัวเลือกหลักเบื้องต้น"],
        "backups": [item for item in labeled if item["status"] == "ไม่เน้นเพราะเป็นเขต 1"],
        "note": note,
        "change_if": [
            "จำนวนผู้สมัครเอกภาษาอังกฤษปี 2569 รายเขต",
            "หน้ารวบรวมการเรียกบรรจุปี 2566 และปี 2568",
            "อัตราเกษียณเอกภาษาอังกฤษรายเขต ไฟล์ที่มีอยู่เป็นยอดทุกวิชา จึงยังไม่ถูกใช้",
            "จำนวนคนย้ายเข้า โดยเฉพาะเขต 1",
            "การคลี่ข้อขัดแย้งของตัวเลขเรียกบรรจุที่แหล่งไม่ตรงกัน",
        ],
    }
