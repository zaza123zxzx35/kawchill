from __future__ import annotations

import io
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.parsing import clean_text, parse_thai_date

RECRUITMENT_HINTS = ("รับสมัคร", "ประกาศรับสมัคร")
CALL_HINTS = ("เรียกบรรจุ", "เรียกตัว", "ขึ้นบัญชี")
SUBJECT_WORD = "ภาษาอังกฤษ"


def meta_refresh_url(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html or "", "html.parser")
    for meta in soup.select("meta[http-equiv]"):
        if clean_text(meta.get("http-equiv")).lower() != "refresh":
            continue
        match = re.search(r"url\s*=\s*['\"]?([^'\";]+)", meta.get("content") or "", flags=re.I)
        if not match:
            continue
        target = urljoin(page_url, match.group(1).strip())
        if urlsplit(target).scheme in {"http", "https"}:
            return target
    return None


def same_site(origin: str, target: str) -> bool:
    base = urlsplit(origin).netloc.lower().removeprefix("www.")
    host = urlsplit(target).netloc.lower().removeprefix("www.")
    return bool(base) and (host == base or host.endswith("." + base))


def find_past_call_links(html: str, page_url: str) -> list[dict]:
    """Older call notices only. A missing year is skipped instead of guessed."""
    found = []
    seen = set()
    soup = BeautifulSoup(html or "", "html.parser")
    for anchor in soup.select("a[href]"):
        label = clean_text(anchor.get_text(" ", strip=True))
        href = urljoin(page_url, anchor.get("href") or "")
        if not label or href in seen or not same_site(page_url, href):
            continue
        if "ผู้บริหาร" in label or "กรณีพิเศษ" in label or "ครูผู้ช่วย" not in label:
            continue
        if not any(hint in label for hint in CALL_HINTS):
            continue
        years = [year for year in re.findall(r"25\d{2}", label) if year in {"2566", "2567", "2568"}]
        if not years:
            continue
        seen.add(href)
        found.append({"kind": "call_notice", "url": href, "title": label, "exam_year": int(years[-1])})
    return found[:2]


def find_notice_links(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    found = []
    seen = set()
    for anchor in soup.select("a[href]"):
        label = clean_text(anchor.get_text(" ", strip=True))
        href = urljoin(page_url, anchor.get("href") or "")
        if not label or href in seen or not same_site(page_url, href):
            continue
        if "ผู้บริหาร" in label or "กรณีพิเศษ" in label:
            continue
        if "ครูผู้ช่วย" not in label:
            continue
        if any(hint in label for hint in RECRUITMENT_HINTS):
            kind = "recruitment_notice"
        elif any(hint in label for hint in CALL_HINTS):
            kind = "call_notice"
        else:
            continue
        if re.search(r"25\d{2}", label) and "2569" not in label:
            continue
        seen.add(href)
        found.append({"kind": kind, "url": href, "title": label})
    return found[:4]


def parse_notice_page(html: str, page_url: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    title_node = soup.select_one("h1") or soup.title
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
    description = ""
    meta = soup.select_one("meta[name='description']")
    if meta:
        description = clean_text(meta.get("content"))
    special = "กรณีพิเศษ" in title or "กรณีที่มีความจำเป็นหรือมีเหตุพิเศษ" in title
    announced_at = None
    for span in soup.find_all("span"):
        text = clean_text(span.get_text(" ", strip=True))
        if len(text) > 40:
            continue
        announced_at = parse_thai_date(text)
        if announced_at:
            break
    start, end = _application_window(description)
    documents = []
    for anchor in soup.select("a[href]"):
        href = urljoin(page_url, anchor.get("href") or "")
        label = clean_text(anchor.get_text(" ", strip=True))
        blob = f"{label} {href}".lower()
        if ".pdf" not in blob and "download" not in blob:
            continue
        if not same_site(page_url, href):
            continue
        documents.append({"url": href, "title": label or title})
    return {
        "title": title,
        "special": special,
        "announced_at": announced_at,
        "application_start": start,
        "application_end": end,
        "documents": documents[:2],
        "page_url": page_url,
    }


def _application_window(text: str) -> tuple[str | None, str | None]:
    source = text or ""
    match = re.search(r"ระหว่างวันที่\s+(\d{1,2})\s*-\s*(\d{1,2})\s+([ก-๙]+)(?:\s+(\d{4}))?", source)
    if not match:
        return None, None
    year = match.group(4)
    if year is None:
        earlier = re.findall(r"(25\d{2})", source[: match.start()])
        year = earlier[-1] if earlier else None
    if year is None:
        return None, None
    start = parse_thai_date(f"{match.group(1)} {match.group(3)} {year}")
    end = parse_thai_date(f"{match.group(2)} {match.group(3)} {year}")
    if start and end and start <= end:
        return start, end
    return None, None


def extract_pdf_text(payload: bytes) -> str:
    if not payload.startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(payload))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def visible_notice_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    return soup.get_text("\n", strip=True)


def interpret_notice_text(text: str) -> dict:
    """Keep a number only when the same line names English and the count's meaning."""
    thai = sum(1 for char in text or "" if "\u0e01" <= char <= "\u0e5b")
    result = {
        "readable": thai >= 12,
        "openings": None,
        "applicants": None,
        "appointed_count": None,
        "call_rank_reached": None,
        "remaining_count": None,
        "message": "",
    }
    if not result["readable"]:
        result["message"] = "ดาวน์โหลดได้แต่ถอดอักษรไทยจากไฟล์ไม่ได้ จึงไม่ใช้ตัวเลขจากไฟล์นี้"
        return result
    openings = _line_number(text, ("จำนวน", "อัตรา", "ตำแหน่ง"), forbidden=("ผู้สมัคร", "เรียก", "บรรจุ", "คงเหลือ"))
    applicants = _line_number(text, ("ผู้สมัคร",), forbidden=("เรียก", "บรรจุ"))
    appointed = _line_number(text, ("เรียกบรรจุ", "บรรจุแล้ว"), forbidden=("เปิดรับ", "อัตรา"))
    rank = _line_number(text, ("เรียกถึงลำดับ", "ลำดับที่"), forbidden=())
    remaining = _line_number(text, ("คงเหลือ",), forbidden=())
    result.update(
        {
            "openings": openings,
            "applicants": applicants,
            "appointed_count": appointed,
            "call_rank_reached": rank,
            "remaining_count": remaining,
        }
    )
    if all(value is None for value in (openings, applicants, appointed, rank, remaining)):
        result["message"] = "อ่านข้อความได้บางส่วน แต่ไม่พบแถวเอกภาษาอังกฤษที่ระบุจำนวนชัดเจน จึงไม่เติมตัวเลข"
    else:
        result["message"] = "ใช้เฉพาะบรรทัดที่ระบุเอกภาษาอังกฤษและชนิดของตัวเลขในบรรทัดเดียวกัน"
    return result


def _line_number(text: str, labels: tuple[str, ...], forbidden: tuple[str, ...]) -> int | None:
    for raw in (text or "").splitlines():
        line = clean_text(raw)
        if SUBJECT_WORD not in line or not any(label in line for label in labels):
            continue
        if any(word in line for word in forbidden):
            continue
        numbers = [int(item) for item in re.findall(r"(?<![\d.])(\d{1,5})(?![\d.])", line)]
        if len(numbers) == 1:
            return numbers[0]
    return None
