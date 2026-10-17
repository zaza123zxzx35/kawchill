from __future__ import annotations

import re
from datetime import datetime

MONTHS = {
    "มกราคม": 1,
    "กุมภาพันธ์": 2,
    "มีนาคม": 3,
    "เมษายน": 4,
    "พฤษภาคม": 5,
    "มิถุนายน": 6,
    "กรกฎาคม": 7,
    "สิงหาคม": 8,
    "กันยายน": 9,
    "ตุลาคม": 10,
    "พฤศจิกายน": 11,
    "ธันวาคม": 12,
}

_MISSING_TOKENS = {"", "-", "–", "—", "−", "ไม่มีข้อมูล", "n/a", "na", "null"}


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_number(value: str | None) -> tuple[int | None, bool, str | None]:
    """Return (number, known, warning).

    A dash means the source has no figure. It is unknown, not zero.
    """
    text = clean_text(value).replace(",", "").replace(" ", "")
    text = text.replace("−", "-").replace("–", "-")
    if text.lower() in _MISSING_TOKENS or text == "ไม่เปิดสอบ":
        return None, False, None
    if re.fullmatch(r"-?\d+", text):
        return int(text), True, None
    return None, False, f"ตัวเลขไม่ถูกต้อง: {clean_text(value)}"


def buddhist_to_gregorian(year: int) -> int:
    return year - 543 if year > 2400 else year


def parse_thai_datetime(text: str) -> str | None:
    match = re.search(
        r"วันที่\s+(\d{1,2})\s+([ก-๙]+)\s+(\d{4})\s+เวลา\s+(\d{1,2}):(\d{2})",
        text,
    )
    if not match:
        return None
    day = int(match.group(1))
    month_name = match.group(2)
    month = MONTHS.get(month_name)
    if not month:
        return None
    year = buddhist_to_gregorian(int(match.group(3)))
    hour = int(match.group(4))
    minute = int(match.group(5))
    try:
        stamp = datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    return stamp.isoformat(timespec="minutes") + "+07:00"


def parse_thai_date(text: str) -> str | None:
    match = re.search(r"(\d{1,2})\s+([ก-๙]+)\s+(\d{4})", text or "")
    if not match:
        return None
    day = int(match.group(1))
    month = MONTHS.get(match.group(2))
    if not month:
        return None
    year = buddhist_to_gregorian(int(match.group(3)))
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def parse_slash_date(text: str) -> str | None:
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    year = buddhist_to_gregorian(year)
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def format_thai_stamp(value: str | None) -> str:
    if not value:
        return MISSING_STAMP
    raw = value.replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return value
    if stamp.tzinfo is not None:
        from datetime import timezone, timedelta

        stamp = stamp.astimezone(timezone(timedelta(hours=7)))
    month = next(name for name, number in MONTHS.items() if number == stamp.month)
    year = stamp.year + 543
    clock = stamp.strftime("%H:%M")
    if stamp.hour == 0 and stamp.minute == 0 and len(value) <= 10:
        return f"{stamp.day} {month} {year}"
    return f"{stamp.day} {month} {year} เวลา {clock} น."


MISSING_STAMP = "ยังไม่มีวันเวลา"


def org_level(area_name: str) -> str:
    name = clean_text(area_name)
    if name.startswith("สพป"):
        return "สพป."
    if name.startswith("สพม"):
        return "สพม."
    if name.startswith("สศศ"):
        return "สศศ."
    return "อื่น ๆ"


def competition_text(applicants: int | None, openings: int | None, known: bool) -> str:
    from src.labels import MISSING

    if not known or applicants is None or openings is None or openings <= 0:
        return MISSING
    ratio = applicants / openings
    return f"1 : {ratio:.1f}"
