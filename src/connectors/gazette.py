from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.labels import EXAM_EXISTING_LIST, EXAM_GENERAL, EXAM_NEWS, EXAM_OTHER_ACCOUNT, EXAM_SPECIAL, SUBJECT
from src.parsing import clean_text, parse_thai_date

PERSONNEL_URL = "https://personnel.obec.go.th/web/archives/category/eoffice"
PERSONNEL_PAGE_2 = "https://personnel.obec.go.th/web/archives/category/eoffice/page/2"
OBEC_TAG_URL = "https://www.obec.go.th/tag/ครูผู้ช่วย/"
PRD_URL = "https://pscd.prd.go.th/th/content/category/detail/id/39/iid/550518"
CIRCULAR_URL = "https://circular.otepc.go.th/"

NOTICE_SOURCES = [
    {"code": "personnel_eoffice", "url": PERSONNEL_URL, "name": "สพร. สพฐ. หนังสือราชการ"},
    {"code": "personnel_eoffice", "url": PERSONNEL_PAGE_2, "name": "สพร. สพฐ. หนังสือราชการ หน้า 2"},
    {"code": "obec_assistant", "url": OBEC_TAG_URL, "name": "สพฐ. ป้ายกำกับครูผู้ช่วย"},
    {"code": "prd_teacher", "url": PRD_URL, "name": "ประชาสัมพันธ์ภาครัฐ ข่าวครูผู้ช่วย"},
    {"code": "otepc_circular", "url": CIRCULAR_URL, "name": "หนังสือเวียน ก.ค.ศ."},
]


def squeeze_thai(text: str) -> str:
    return re.sub(r"(?<=[\u0e00-\u0e7f])[ \t]+(?=[\u0e00-\u0e7f])", "", text or "")


def norm_area(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def classify_exam_type(title: str) -> str:
    text = title or ""
    if "กรณีพิเศษ" in text or "กรณีที่มีความจำเป็น" in text:
        return EXAM_SPECIAL
    if "ใช้บัญชี" in text and "เขต" in text:
        return EXAM_OTHER_ACCOUNT
    if "บัญชีคงเหลือ" in text or "คงเหลือ" in text:
        return EXAM_EXISTING_LIST
    if any(word in text for word in ("รับสมัคร", "สอบแข่งขัน", "กำหนดการสอบ")):
        return EXAM_GENERAL
    return EXAM_NEWS


def exam_year_from_text(text: str) -> int | None:
    short = re.search(r"ปี[\s\-]*(\d{2})(?!\d)", text or "")
    if short and short.group(1) in {"66", "67", "68", "69"}:
        return 2500 + int(short.group(1))
    full = re.search(r"25(6[6-9])", text or "")
    if full:
        return int(full.group(0))
    return None


def parse_notice_listing(html: str, page_url: str) -> list[dict]:
    """Keep assistant-teacher links only. Other circulars stay off the list."""
    soup = BeautifulSoup(html or "", "html.parser")
    found = []
    seen = set()
    for anchor in soup.select("a[href]"):
        label = clean_text(anchor.get_text(" ", strip=True))
        href = urljoin(page_url, anchor.get("href") or "")
        blob = f"{label} {href}"
        if "ครูผู้ช่วย" not in blob or href in seen:
            continue
        if any(skip in label for skip in ("หน้าหลัก", "ฟีด", "ค้นหา")):
            continue
        seen.add(href)
        kind = "pdf" if ".pdf" in href.lower() else "article"
        found.append(
            {
                "title": label or href.rsplit("/", 1)[-1],
                "url": href,
                "doc_kind": kind,
                "exam_type": classify_exam_type(blob),
                "exam_year": exam_year_from_text(blob),
                "announced_at": parse_thai_date(label),
            }
        )
    title_node = soup.select_one("h1") or soup.title
    page_title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
    if "ครูผู้ช่วย" in page_title and page_url not in seen:
        found.insert(
            0,
            {
                "title": page_title,
                "url": page_url,
                "doc_kind": "article",
                "exam_type": classify_exam_type(page_title),
                "exam_year": exam_year_from_text(page_title),
                "announced_at": parse_thai_date(page_title),
            },
        )
    if not found and "ครูผู้ช่วย" in (html or ""):
        title_node = soup.select_one("h1") or soup.title
        title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        found.append(
            {
                "title": title or page_url,
                "url": page_url,
                "doc_kind": "article",
                "exam_type": classify_exam_type(title),
                "exam_year": exam_year_from_text(title),
                "announced_at": parse_thai_date(clean_text(soup.get_text(" ", strip=True))[:80]),
            }
        )
    return found


def extraction_status(text: str) -> str:
    thai = sum(1 for char in text or "" if "\u0e01" <= char <= "\u0e5b")
    if thai < 12:
        return "review"
    return "text"


def document_matches(row: dict, keyword: str = "") -> bool:
    """Match a stored notice by subject, district name, area key, or another Thai keyword."""
    needle = (keyword or "").strip()
    if not needle:
        return True
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("subject", "area_name", "area_key", "title", "message", "url", "evidence", "exam_type")
    )
    return needle in blob


def pdf_links(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    found = []
    seen = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(page_url, anchor.get("href") or "")
        if ".pdf" not in href.lower() or href in seen:
            continue
        seen.add(href)
        label = clean_text(anchor.get_text(" ", strip=True)) or href.rsplit("/", 1)[-1]
        found.append(
            {
                "title": label,
                "url": href,
                "doc_kind": "pdf",
                "exam_type": classify_exam_type(f"{label} {href}"),
                "exam_year": exam_year_from_text(f"{label} {href}"),
                "announced_at": None,
            }
        )
    return found[:2]


def parse_english_account_table(text: str) -> list[dict]:
    """English column only. A row is kept when listed - appointed = remaining."""
    lines = [clean_text(line) for line in squeeze_thai(text).splitlines()]
    lines = [line for line in lines if line]
    subjects = []
    for line in lines:
        if re.fullmatch(r"\d+", line) and subjects:
            break
        if line in {"ขึ้นบัญชี", "เรียกบรรจุ", "คงเหลือ"}:
            break
        if any(token in line for token in ("ภาษา", "คณิต", "วิทยาศาสตร์", "สังคม", "ดนตรี", "นาฏ", "ประถม", "ปฐม")):
            subjects.append(line)
    if SUBJECT not in subjects:
        return []
    english_index = subjects.index(SUBJECT)
    grouped: dict[str, dict] = {}
    pattern = re.compile(r"^\d+\s+((?:สพป\.|สพม\.|สศศ\.).*?)(ขึ้นบัญชี|เรียกบรรจุ|คงเหลือ)\s+((?:\d+\s*)+)$")
    for line in lines:
        match = pattern.match(line)
        if not match:
            continue
        area_name = norm_area(match.group(1))
        kind = match.group(2)
        numbers = [int(item) for item in match.group(3).split()]
        if len(numbers) != len(subjects):
            continue
        bucket = grouped.setdefault(area_name, {"area_name": area_name, "snippet": []})
        bucket[{"ขึ้นบัญชี": "listed", "เรียกบรรจุ": "appointed", "คงเหลือ": "remaining"}[kind]] = numbers[english_index]
        bucket["snippet"].append(line)
    rows = []
    for bucket in grouped.values():
        listed = bucket.get("listed")
        appointed = bucket.get("appointed")
        remaining = bucket.get("remaining")
        consistent = (
            listed is not None
            and appointed is not None
            and remaining is not None
            and listed - appointed == remaining
        )
        rows.append(
            {
                "area_name": bucket["area_name"],
                "listed": listed,
                "appointed": appointed,
                "remaining": remaining,
                "consistent": consistent,
                "snippet": " | ".join(bucket["snippet"])[:500],
                "subject": SUBJECT,
            }
        )
    return rows


def parse_english_account_pdf(payload: bytes) -> list[dict]:
    """Read the English column by its horizontal position, so blank cells do not shift later subjects."""
    import io

    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTAnno, LTChar, LTTextLine

    grouped: dict[str, dict] = {}
    english_x = None
    for page in extract_pages(io.BytesIO(payload)):
        boxes = []
        chars = []

        def walk(element) -> None:
            if isinstance(element, LTChar):
                chars.append((element.x0, element.y0, element.get_text()))
                return
            if isinstance(element, LTTextLine):
                text = element.get_text().replace("\n", "").strip()
                if text:
                    boxes.append((element.x0, element.y0, text))
            if hasattr(element, "__iter__") and not isinstance(element, LTAnno):
                for child in element:
                    walk(child)

        walk(page)
        boxes = _unique_boxes(boxes)
        found = _english_header_x(boxes) or _english_x_from_chars(chars)
        if found is not None:
            english_x = found
        if english_x is None:
            continue
        _apply_account_page(grouped, english_x, _cluster_rows(boxes))
    return account_rows_from_grouped(grouped)


def account_rows_from_grouped(grouped: dict[str, dict]) -> list[dict]:
    rows = []
    for bucket in grouped.values():
        listed = bucket.get("listed")
        appointed = bucket.get("appointed")
        remaining = bucket.get("remaining")
        rows.append(
            {
                "area_name": bucket["area_name"],
                "listed": listed,
                "appointed": appointed,
                "remaining": remaining,
                "consistent": account_consistent(listed, appointed, remaining),
                "snippet": " | ".join(bucket["snippet"])[:500],
                "subject": SUBJECT,
            }
        )
    return rows


def account_consistent(listed: int | None, appointed: int | None, remaining: int | None) -> bool:
    return (
        listed is not None
        and appointed is not None
        and remaining is not None
        and listed - appointed == remaining
    )


def font_key(name: str) -> str:
    """Fold only the font substitutions seen in these PDFs. Not a fuzzy name match."""
    text = norm_area(name).replace("\u0e33", "\u0e32")
    return re.sub(r"[\u0e48-\u0e4b]", "", text)


def match_area_name(areas: dict[str, tuple[str, str]], raw_name: str) -> tuple[str, str] | None:
    exact = areas.get(norm_area(raw_name))
    if exact is not None:
        return exact
    wanted = font_key(raw_name)
    hits = []
    for stored_name, item in areas.items():
        if font_key(stored_name) == wanted or font_key(item[1]) == wanted:
            hits.append(item)
    unique = {item[0]: item for item in hits}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def _unique_boxes(boxes: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    seen = set()
    unique = []
    for x, y, text in boxes:
        key = (round(x, 1), round(y, 1), text)
        if key in seen:
            continue
        seen.add(key)
        unique.append((x, y, text))
    return unique


def _english_header_x(boxes: list[tuple[float, float, str]]) -> float | None:
    for x, _y, text in boxes:
        compact = squeeze_thai(text)
        if "อัง" in compact and 90 <= x <= 280:
            return x
    return None


def _english_x_from_chars(chars: list[tuple[float, float, str]]) -> float | None:
    """Year 68 prints subject names as separate vertical glyphs, so no text box contains อังกฤษ."""
    clusters: dict[float, list[tuple[float, str]]] = {}
    for x, y, char in chars:
        if y < 450 or not (90 <= x <= 280):
            continue
        key = round(x)
        placed = None
        for existing in clusters:
            if abs(existing - key) <= 2:
                placed = existing
                break
        clusters.setdefault(placed if placed is not None else key, []).append((y, char))
    for key, items in clusters.items():
        text = "".join(char for _y, char in sorted(items))
        bare = re.sub(r"[\u0e31-\u0e4e]", "", text)
        if "ภษองกฤษ" in bare or "ภษองกฤษ" in bare[::-1] or "อังกฤษ" in text or "ฤษกงัอา" in text:
            return float(key)
    return None


def _cluster_rows(boxes: list[tuple[float, float, str]]) -> dict[float, list[tuple[float, str]]]:
    rows: dict[float, list[tuple[float, str]]] = {}
    for x, y, text in boxes:
        key = round(y)
        placed = None
        for existing in rows:
            if abs(existing - key) <= 2:
                placed = existing
                break
        rows.setdefault(placed if placed is not None else key, []).append((x, text))
    return rows


def _apply_account_page(grouped: dict[str, dict], english_x: float, clustered: dict[float, list[tuple[float, str]]]) -> None:
    """The 2568 file prints the district name only on the first of the three status rows."""
    current = None
    for _y, row in sorted(clustered.items(), key=lambda item: -item[0]):
        name, kind, english_value = _inspect_account_row(row, english_x)
        if name:
            current = name
        if kind is None or current is None:
            continue
        bucket = grouped.setdefault(current, {"area_name": current, "snippet": []})
        if english_value is not None:
            bucket[kind] = english_value
        bucket["snippet"].append(f"{kind} {english_value if english_value is not None else 'ว่าง'}")


def _inspect_account_row(row: list[tuple[float, str]], english_x: float) -> tuple[str | None, str | None, int | None]:
    pieces = []
    kind = None
    english_value = None
    for x, text in sorted(row, key=lambda item: item[0]):
        compact = squeeze_thai(text)
        if "นบัญชี" in compact:
            kind = "listed"
        elif "เรียกบรรจุ" in compact:
            kind = "appointed"
        elif "คงเหลือ" in compact:
            kind = "remaining"
        if re.fullmatch(r"\d+", compact) and abs(x - english_x) <= 7:
            english_value = int(compact)
        if x < 90:
            pieces.append(compact)
    area_name = norm_area("".join(pieces))
    area_name = re.sub(r"^\d+", "", area_name)
    area_name = re.sub(r"(เรียกบรรจุ|คงเหลือ|ขึ้นบัญชี|ขึ้น|ขึ).*$", "", area_name)
    if not area_name.startswith(("สพป.", "สพม.", "สศศ.")):
        area_name = None
    return area_name, kind, english_value


def _read_account_row(row: list[tuple[float, str]], english_x: float, grouped: dict[str, dict]) -> None:
    _apply_account_page(grouped, english_x, {0: row})
