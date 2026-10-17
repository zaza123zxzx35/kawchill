from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.labels import AFFILIATION, EXAM_GENERAL, EXAM_SPECIAL, SUBJECT
from src.parsing import clean_text, org_level, parse_number, parse_slash_date, parse_thai_datetime

REPORT_URL = "https://www.kruphuchuay.com/report.php?r=4/5"
RELATED_URLS = {
    "ทุกสังกัด": REPORT_URL,
    "ประถมศึกษา": "https://www.kruphuchuay.com/report.php?r=4/5/1",
    "มัธยมศึกษา": "https://www.kruphuchuay.com/report.php?r=4/5/2",
    "การศึกษาพิเศษ": "https://www.kruphuchuay.com/report.php?r=4/5/3",
}
AREA_INDEX_URL = "https://www.kruphuchuay.com/report.php?r=1"
WAITING_URL = "https://www.kruphuchuay.com/report.php?r=6"
CLOSED_URL = "https://www.kruphuchuay.com/report.php?r=5"
BASE = "https://www.kruphuchuay.com/"


@dataclass
class SubjectParse:
    structure_ok: bool
    exam_type: str
    reference_at: str | None
    description: str
    records: list[dict] = field(default_factory=list)
    summaries: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pending_areas: int | None = None


def parse_subject_report(html: str, source_url: str = REPORT_URL) -> SubjectParse:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    title = clean_text(soup.title.get_text()) if soup.title else ""
    lead = text[:1800]
    exam_type = EXAM_SPECIAL if ("กรณีพิเศษ" in title or "กรณีที่มีความจำเป็นหรือมีเหตุพิเศษ" in lead) else EXAM_GENERAL
    reference_at = parse_thai_datetime(text)
    description_match = re.search(r"(การคัดเลือกบุคคลเพื่อบรรจุ.{10,220}?พ\.ศ\.\s*\d{4})", text)
    description = clean_text(description_match.group(1)) if description_match else clean_text(title)
    warnings: list[str] = []
    if exam_type == EXAM_SPECIAL:
        warnings.append("หน้านี้เป็นกรณีพิเศษ จึงไม่นำไปรวมกับรอบการคัดเลือกทั่วไป")

    rows = []
    for tr in soup.select("tbody tr"):
        name_node = tr.select_one("td.c-name .n")
        if name_node is None:
            continue
        area_name = clean_text(name_node.get_text())
        if not area_name:
            continue
        link = tr.select_one("a[href*='report.php?r=2/']")
        href = link.get("href", "") if link else ""
        area_key = ""
        detail_url = None
        if href:
            detail_url = urljoin(source_url, href)
            key_match = re.search(r"r=2/(\d+)", href)
            area_key = f"2/{key_match.group(1)}" if key_match else href
        if not area_key:
            area_key = area_name
        year_cells: dict[int, dict] = {}
        for cell in tr.select("td[data-label]"):
            label = clean_text(cell.get("data-label"))
            year_match = re.search(r"(\d{4})", label)
            if not year_match:
                warnings.append(f"{area_name}: ป้ายปีไม่ชัดเจน ({label})")
                continue
            year = int(year_match.group(1))
            bucket = year_cells.setdefault(
                year,
                {
                    "opened": None,
                    "openings": None,
                    "applicants": None,
                    "applicants_known": False,
                    "listed_count": None,
                    "appointed_count": None,
                },
            )
            classes = " ".join(cell.get("class") or [])
            cell_text = clean_text(cell.get_text())
            if "none-yr" in classes or cell_text == "ไม่เปิดสอบ" or label.startswith("ปี "):
                bucket["opened"] = False
                continue
            number, known, warning = parse_number(cell_text)
            if warning:
                warnings.append(f"{area_name} ปี {year}: {warning}")
            if number is not None and number < 0:
                warnings.append(f"{area_name} ปี {year}: ตัวเลขติดลบ")
                number, known = None, False
            if label.startswith("เปิดรับ"):
                bucket["openings"] = number
                bucket["opened"] = True if known else bucket["opened"]
            elif label.startswith("สมัคร"):
                bucket["applicants"] = number if known else None
                bucket["applicants_known"] = known
            elif label.startswith("ขึ้นบัญชี"):
                bucket["listed_count"] = number if known else None
            elif label.startswith("บรรจุ"):
                bucket["appointed_count"] = number if known else None
        for year, bucket in year_cells.items():
            listed = bucket["listed_count"]
            appointed = bucket["appointed_count"]
            remaining = None
            if listed is not None and appointed is not None:
                if appointed > listed:
                    warnings.append(f"{area_name} ปี {year}: เรียกบรรจุมากกว่าจำนวนขึ้นบัญชี")
                else:
                    remaining = listed - appointed
            rows.append(
                {
                    "area_name": area_name,
                    "area_key": area_key,
                    "org_level": org_level(area_name),
                    "exam_year": year,
                    "exam_type": exam_type,
                    "subject": SUBJECT,
                    "affiliation": AFFILIATION,
                    "opened": bucket["opened"],
                    "openings": bucket["openings"],
                    "applicants": bucket["applicants"],
                    "applicants_known": bool(bucket["applicants_known"]),
                    "listed_count": listed,
                    "appointed_count": appointed,
                    "remaining_count": remaining,
                    "detail_url": detail_url,
                    "source_url": source_url,
                    "reference_at": reference_at,
                }
            )

    deduped: list[dict] = []
    seen: set[tuple] = set()
    for row in rows:
        key = (row["exam_type"], row["exam_year"], row["area_key"])
        if key in seen:
            warnings.append(f"ข้อมูลซ้ำ: {row['area_name']} ปี {row['exam_year']}")
            continue
        seen.add(key)
        deduped.append(row)

    structure_ok = bool(deduped) and SUBJECT in html and "เปิดรับ" in html
    if not structure_ok:
        warnings.append("โครงสร้างตารางเปลี่ยน หรือไม่พบแถวเขตพื้นที่")

    summaries, pending = _parse_summaries(html, exam_type)
    return SubjectParse(
        structure_ok=structure_ok,
        exam_type=exam_type,
        reference_at=reference_at,
        description=description,
        records=deduped if structure_ok else [],
        summaries=summaries if structure_ok else [],
        warnings=warnings,
        pending_areas=pending,
    )


def _parse_summaries(html: str, exam_type: str) -> tuple[list[dict], int | None]:
    by_year: dict[int, dict] = {}

    def bucket(year: int) -> dict:
        return by_year.setdefault(
            year,
            {
                "exam_year": year,
                "exam_type": exam_type,
                "subject": SUBJECT,
                "areas_opened": None,
                "openings": None,
                "applicants": None,
                "applicants_known": False,
                "listed_count": None,
                "appointed_count": None,
                "remaining_count": None,
                "note": None,
            },
        )

    for label in re.findall(r"aria-label=(['\"])(.+?)\1", html):
        label = label[1]
        kind = None
        if label.startswith("เขตที่เปิดสอบ"):
            kind = "areas_opened"
        elif label.startswith("ตำแหน่งที่เปิดรับ"):
            kind = "openings"
        elif label.startswith("ผู้สมัคร"):
            kind = "applicants"
        else:
            continue
        for year_text, raw in re.findall(r"ปี\s*(\d{4})\s+([\d,]+|ไม่มีข้อมูล)", label):
            year = int(year_text)
            item = bucket(year)
            if raw == "ไม่มีข้อมูล":
                if kind == "applicants":
                    item["applicants_known"] = False
                    item["applicants"] = None
                continue
            number, known, _warning = parse_number(raw)
            if not known:
                continue
            item[kind] = number
            if kind == "applicants":
                item["applicants_known"] = True

    for year_text, listed_text, appointed_text, remaining_text in re.findall(
        r"class='yr[^']*'>\s*(\d{4})</span>.*?ขึ้นบัญชี <b>([\d,]+)</b>\s*·\s*บรรจุ <b>([\d,]+)</b>.*?คงเหลือ <b>([\d,]+)</b>",
        html,
        flags=re.S,
    ):
        item = bucket(int(year_text))
        item["listed_count"] = parse_number(listed_text)[0]
        item["appointed_count"] = parse_number(appointed_text)[0]
        item["remaining_count"] = parse_number(remaining_text)[0]

    pending_match = re.search(r"เขตรอประกาศ\s+([\d,]+)\s+เขต", html)
    pending = parse_number(pending_match.group(1))[0] if pending_match else None
    if pending is not None and 2569 in by_year:
        by_year[2569]["note"] = f"ยังมีเขตรอประกาศ {pending} เขต ตัวเลขปีนี้อาจยังไม่ครบ"

    summaries = [by_year[year] for year in sorted(by_year)]
    return summaries, pending


def shorten_area_name(value: str) -> str:
    text = unicodedata.normalize("NFC", value or "").replace("สํา", "สำ")
    text = clean_text(text)
    if text == "สำนักบริหารงานการศึกษาพิเศษ":
        return text
    for prefix, short in (
        ("สำนักงานเขตพื้นที่การศึกษาประถมศึกษา", "สพป."),
        ("สำนักงานเขตพื้นที่การศึกษามัธยมศึกษา", "สพม."),
        ("สำนักงานเขตพื้นที่การศึกษาพิเศษ", "สศศ."),
    ):
        if text.startswith(prefix):
            return clean_text(short + text[len(prefix):])
    return text


def parse_area_name_list(html: str) -> tuple[list[str], str | None, bool]:
    """Read district names from an area-status page.

    Returns names, the page's data date, and whether the expected list markup exists.
    An empty list with the markup present is a real empty result. Missing markup is unverified.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    reference_at = parse_thai_datetime(text)
    cards = soup.select(".esa-card .n")
    if cards:
        names = []
        seen = set()
        for node in cards:
            name = shorten_area_name(node.get_text(" ", strip=True))
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names, reference_at, True
    rows = soup.select("tbody tr td.c-name .n")
    if rows:
        names = []
        seen = set()
        for node in rows:
            name = shorten_area_name(node.get_text(" ", strip=True))
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names, reference_at, True
    return [], reference_at, False


def parse_area_detail(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    result = {
        "structure_ok": False,
        "actual_count": None,
        "standard_count": None,
        "gap_count": None,
        "openings": None,
        "website": None,
        "reference_at": None,
        "warnings": [],
    }
    row = soup.select_one("tr[data-search='ภาษาอังกฤษ']")
    if row is None:
        result["warnings"].append("ไม่พบแถวภาษาอังกฤษในหน้ารายเขต")
        return result
    result["structure_ok"] = True
    sub = row.select_one("small.std-sub")
    if sub:
        match = re.search(r"มี\s*([\d,]+)\s*/\s*ควรมี\s*([\d,]+)", sub.get_text())
        if match:
            actual, actual_known, actual_warn = parse_number(match.group(1))
            standard, standard_known, standard_warn = parse_number(match.group(2))
            if actual_warn or standard_warn:
                result["warnings"].append(actual_warn or standard_warn)
            if actual_known and standard_known:
                result["actual_count"] = actual
                result["standard_count"] = standard
                result["gap_count"] = actual - standard
    opening_cell = row.select_one("td[data-label='เปิดสอบ (ตำแหน่ง)']")
    if opening_cell:
        number, known, warning = parse_number(opening_cell.get_text())
        if warning:
            result["warnings"].append(warning)
        if known:
            result["openings"] = number
    for anchor in soup.select("a[href]"):
        if clean_text(anchor.get_text()) == "เว็บไซต์":
            result["website"] = anchor.get("href")
            break
    dated = re.search(r"ข้อมูล ณ\s+(\d{1,2}/\d{1,2}/\d{4})", soup.get_text(" ", strip=True))
    result["reference_at"] = parse_slash_date(dated.group(1)) if dated else None
    return result
