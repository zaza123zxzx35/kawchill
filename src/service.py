from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from src.connectors.gazette import (
    NOTICE_SOURCES,
    classify_exam_type,
    extraction_status,
    norm_area,
    parse_english_account_table,
    parse_notice_listing,
    pdf_links,
)
from src.connectors.hrms import PAGE_URL as HRMS_PAGE, discover_report_script, parse_english_staffing
from src.connectors.http_client import get_bytes, get_text
from src.connectors.kruphuchuay import (
    AREA_INDEX_URL,
    CLOSED_URL,
    REPORT_URL,
    WAITING_URL,
    parse_area_detail,
    parse_area_name_list,
    parse_subject_report,
)
from src.connectors.notices import (
    extract_pdf_text,
    find_notice_links,
    find_past_call_links,
    interpret_notice_text,
    meta_refresh_url,
    parse_notice_page,
    visible_notice_text,
)
from src.connectors.official import OFFICIAL_SITES, classify_homepage
from src.coverage import build_coverage
from src.db import connect, get_pref, init_db, now_iso, set_pref
from src.labels import (
    COVERAGE_ANNOUNCED,
    COVERAGE_OPEN,
    EXAM_EXISTING_LIST,
    EXAM_GENERAL,
    EXAM_NEWS,
    STATUS_FAIL,
    STATUS_FRESH,
    STATUS_STALE,
    SUBJECT,
)
from src.scoring import AreaInput, YearStat, normalize_weights
from src.staffing import VERIFIED_KEY, link_staffing_records

_WRITE_LOCK = threading.Lock()
COMPARE_FIELDS = (
    "openings",
    "applicants",
    "applicants_known",
    "listed_count",
    "appointed_count",
    "remaining_count",
    "opened",
)


def classify_data_status(last_usable_at: str | None, last_run_status: str | None, interval_hours: float, now: datetime | None = None) -> str:
    failed = {"failed", "structure_changed"}
    if last_run_status in failed or not last_usable_at:
        return STATUS_FAIL
    current = now or datetime.now().astimezone()
    stamp = datetime.fromisoformat(last_usable_at)
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    if current - stamp <= timedelta(hours=interval_hours):
        return STATUS_FRESH
    return STATUS_STALE


def refresh_all(db_path: Path | str, http_get=None) -> list[dict]:
    getter = http_get or get_text
    init_db(db_path)
    if not _acquire_lock(db_path):
        return [{"source": "system", "status": "failed", "message": "มีการดึงข้อมูลอยู่แล้ว"}]
    try:
        results = [
            _guard(db_path, "kruphuchuay", lambda: refresh_kruphuchuay(db_path, getter)),
            _guard(db_path, "hrms_obec", lambda: refresh_hrms(db_path, getter)),
        ]
        results.extend(refresh_officials(db_path, getter))
        if getter is get_text:
            results.append(refresh_notice_sources(db_path))
        return results
    finally:
        _release_lock(db_path)


def _guard(db_path, source: str, action) -> dict:
    try:
        return action()
    except Exception as exc:
        _finish_run(db_path, source, "failed", None, f"ดึงข้อมูลไม่สำเร็จ: {exc}", 0, None, False)
        return {"source": source, "status": "failed", "message": str(exc)}


def refresh_kruphuchuay(db_path, http_get) -> dict:
    started = now_iso()
    status, html = http_get(REPORT_URL)
    if status != 200 or not html:
        _finish_run(db_path, "kruphuchuay", "failed", status, f"เว็บตอบสถานะ {status}", 0, None, False, started)
        return {"source": "kruphuchuay", "status": "failed", "message": f"เว็บตอบสถานะ {status}"}
    parsed = parse_subject_report(html, REPORT_URL)
    digest = hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()
    if not parsed.structure_ok:
        message = " ; ".join(parsed.warnings) or "โครงสร้างหน้าเว็บเปลี่ยน"
        _finish_run(db_path, "kruphuchuay", "structure_changed", status, message, 0, digest, False, started)
        return {"source": "kruphuchuay", "status": "structure_changed", "message": message}
    run_id = _start_run(db_path, "kruphuchuay", started)
    written = _save_recruitment(db_path, "kruphuchuay", run_id, parsed.records)
    _save_summaries(db_path, "kruphuchuay", run_id, parsed.summaries)
    detail_notes = _refresh_area_details(db_path, run_id, parsed.records, http_get)
    coverage_notes = _refresh_coverage(db_path, http_get, parsed.records, parsed.reference_at)
    notice_note = _refresh_area_notices(db_path, http_get)
    warnings = parsed.warnings + detail_notes + coverage_notes
    if notice_note:
        warnings.append(notice_note)
    _replace_warnings(db_path, "kruphuchuay", run_id, warnings)
    partial = any("ไม่สำเร็จ" in note for note in detail_notes)
    state = "partial" if partial else "success"
    message = parsed.description or "ดึงตารางวิชาเอกภาษาอังกฤษแล้ว"
    if parsed.pending_areas is not None:
        message += f" เขตรอประกาศ {parsed.pending_areas} เขต"
    if warnings:
        message += " | " + " ; ".join(warnings[:8])
    _complete_run(db_path, run_id, state, status, message, written, digest, True)
    return {"source": "kruphuchuay", "status": state, "message": message, "records": written}


def _refresh_area_details(db_path, run_id: int, records: list[dict], http_get) -> list[str]:
    urls = {}
    for record in records:
        if record.get("detail_url"):
            urls.setdefault(record["detail_url"], (record["area_name"], record["area_key"]))
    notes = []
    failures = 0

    def pull(item):
        url, (area_name, area_key) = item
        time.sleep(0.05)
        try:
            status, html = http_get(url)
            return area_name, area_key, url, status, html, None
        except Exception as exc:
            return area_name, area_key, url, None, "", exc

    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(pull, list(urls.items())))
    for area_name, area_key, url, status, html, error in fetched:
        if error is not None:
            failures += 1
            notes.append(f"{area_name}: ดึงหน้ารายเขตไม่สำเร็จ ({error})")
            continue
        if status != 200:
            failures += 1
            notes.append(f"{area_name}: หน้ารายเขตตอบสถานะ {status}")
            continue
        detail = parse_area_detail(html)
        if not detail["structure_ok"]:
            failures += 1
            notes.append(f"{area_name}: โครงสร้างหน้ารายเขตเปลี่ยน")
            continue
        if detail["website"]:
            _set_website(db_path, area_name, detail["website"])
        if detail["actual_count"] is not None and detail["standard_count"] is not None:
            _save_staffing(
                db_path,
                "kruphuchuay",
                run_id,
                {
                    "scope": "area",
                    "area_name": area_name,
                    "subject": SUBJECT,
                    "standard_count": detail["standard_count"],
                    "actual_count": detail["actual_count"],
                    "gap_count": detail["gap_count"],
                    "reference_at": detail["reference_at"],
                    "reference_note": "มีครู / ควรมี จากหน้ารายเขตของ kruphuchuay ซึ่งอ้างบัญชีอัตรากำลังรายเขต ไม่ใช่จำนวนตำแหน่งสอบ",
                    "source_url": url,
                    "area_key": area_key,
                    "verify_status": VERIFIED_KEY,
                    "raw": {"openings_on_detail": detail["openings"]},
                },
            )
        current = next((row["openings"] for row in records if row["area_name"] == area_name and row["exam_year"] == 2569), None)
        if detail["openings"] is not None and current is not None and detail["openings"] != current:
            notes.append(f"{area_name}: ตำแหน่งปี 2569 บนหน้ารวมเป็น {current} แต่หน้ารายเขตเป็น {detail['openings']}")
    if failures:
        notes.append(f"ดึงหน้ารายเขตไม่สำเร็จ {failures} จาก {len(urls)} เขต")
    return notes


def _refresh_coverage(db_path, http_get, records: list[dict], reference_at: str | None) -> list[str]:
    started = now_iso()
    waiting, waiting_ok, waiting_error = _fetch_name_list(http_get, WAITING_URL)
    closed, closed_ok, closed_error = _fetch_name_list(http_get, CLOSED_URL)
    indexed, index_ok, index_error = _fetch_name_list(http_get, AREA_INDEX_URL)
    complete = waiting_ok and closed_ok and index_ok
    rows, warnings = build_coverage(
        records,
        waiting if waiting_ok else None,
        closed if closed_ok else None,
        indexed if index_ok else None,
        reference_at=reference_at,
        subject_url=REPORT_URL,
        waiting_url=WAITING_URL,
        closed_url=CLOSED_URL,
        index_url=AREA_INDEX_URL,
    )
    _save_coverage(db_path, rows, replace_all=complete)
    problems = [item for item in (waiting_error, closed_error, index_error) if item]
    state = "success" if complete else "failed"
    message = f"บันทึกสถานะประกาศ {len(rows)} เขต" if complete else " ; ".join(problems + warnings)
    _finish_run(db_path, "kruphuchuay_coverage", state, 200 if complete else None, message, len(rows), None, complete, started)
    return warnings + problems


def _fetch_name_list(http_get, url: str) -> tuple[list[str], bool, str | None]:
    try:
        status, html = http_get(url)
    except Exception as exc:
        return [], False, f"ตรวจสอบ {url} ไม่ได้: {exc}"
    if status != 200:
        return [], False, f"หน้า {url} ตอบสถานะ {status}"
    names, _reference, found = parse_area_name_list(html)
    if not found:
        return [], False, f"โครงสร้างรายชื่อที่ {url} เปลี่ยน"
    return names, True, None


def _save_coverage(db_path, rows: list[dict], replace_all: bool) -> None:
    with connect(db_path) as conn:
        if replace_all:
            conn.execute(
                """
                DELETE FROM coverage_records
                WHERE source_code = 'kruphuchuay' AND exam_type = ? AND subject = ? AND exam_year = 2569
                """,
                (EXAM_GENERAL, SUBJECT),
            )
        for row in rows:
            conn.execute(
                """
                INSERT INTO coverage_records (
                    source_code, exam_year, exam_type, subject, area_key, area_name, status, evidence,
                    source_url, announced_at, application_start, application_end, observed_at
                ) VALUES ('kruphuchuay', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_code, exam_type, subject, exam_year, area_key) DO UPDATE SET
                    area_name=excluded.area_name, status=excluded.status, evidence=excluded.evidence,
                    source_url=excluded.source_url, announced_at=excluded.announced_at,
                    application_start=excluded.application_start, application_end=excluded.application_end,
                    observed_at=excluded.observed_at
                """,
                (
                    row["exam_year"],
                    row["exam_type"],
                    row["subject"],
                    row["area_key"],
                    row["area_name"],
                    row["status"],
                    row["evidence"],
                    row["source_url"],
                    row["announced_at"],
                    row["application_start"],
                    row["application_end"],
                    now_iso(),
                ),
            )


def _refresh_area_notices(db_path, http_get) -> str:
    if http_get is not get_text:
        return ""
    started = now_iso()
    with connect(db_path) as conn:
        areas = conn.execute(
            """
            SELECT area_key, area_name, area_website, openings
            FROM recruitment_records
            WHERE source_code = 'kruphuchuay' AND exam_year = 2569 AND exam_type = ? AND subject = ? AND opened = 1
            """,
            (EXAM_GENERAL, SUBJECT),
        ).fetchall()
    areas = [dict(row) for row in areas]
    counts = {"checked": 0, "failed": 0, "unreadable": 0, "notice": 0, "no_notice": 0}

    def visit(area):
        website = area["area_website"]
        if not website:
            _save_document(db_path, area, "", "area_homepage", "unverified", "หน้าต้นทางไม่มีลิงก์เว็บไซต์ของเขตนี้")
            with _WRITE_LOCK:
                counts["failed"] += 1
            return
        with _WRITE_LOCK:
            counts["checked"] += 1
        try:
            _inspect_area_site(db_path, area, website, counts)
        except Exception as exc:
            with _WRITE_LOCK:
                counts["failed"] += 1
            _save_document(db_path, area, website, "area_homepage", "fetch_failed", f"เปิดเว็บเขตไม่ได้: {exc}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(visit, list(areas)))
    message = (
        f"ตรวจเว็บเขต {counts['checked']} เขต พบหน้าประกาศ {counts['notice']} "
        f"ไม่พบลิงก์ประกาศ {counts['no_notice']} อ่านไฟล์ไม่ได้ {counts['unreadable']} เปิดเว็บไม่ได้ {counts['failed']}"
    )
    state = "failed" if counts["checked"] and counts["failed"] == counts["checked"] else "success"
    if counts["failed"] or counts["unreadable"]:
        state = "partial"
    _finish_run(db_path, "area_websites", state, None, message, counts["notice"], None, True, started)
    return message


def _inspect_area_site(db_path, area, website: str, counts: dict) -> None:
    status, html = get_text(website, timeout=12)
    if status != 200 or not html:
        with _WRITE_LOCK:
            counts["failed"] += 1
        _save_document(db_path, area, website, "area_homepage", "fetch_failed", f"เว็บเขตตอบสถานะ {status}")
        return
    refresh = meta_refresh_url(html, website)
    if refresh and len(html) < 2000:
        status, html = get_text(refresh, timeout=12)
        website = refresh
        if status != 200:
            with _WRITE_LOCK:
                counts["failed"] += 1
            _save_document(db_path, area, website, "area_homepage", "fetch_failed", f"หน้าต่อไปของเว็บเขตตอบสถานะ {status}")
            return
    links = find_notice_links(html, website)
    if not links:
        with _WRITE_LOCK:
            counts["no_notice"] += 1
        _save_document(db_path, area, website, "area_homepage", "no_notice", "ไม่พบลิงก์ประกาศรับสมัครหรือเรียกบรรจุครูผู้ช่วยบนหน้าแรก")
        return
    with _WRITE_LOCK:
        counts["notice"] += 1
    for link in links[:2]:
        page_status, page = get_text(link["url"], timeout=12)
        if page_status != 200:
            _save_document(db_path, area, link["url"], link["kind"], "fetch_failed", f"หน้าประกาศตอบสถานะ {page_status}")
            continue
        notice = parse_notice_page(page, link["url"])
        if notice["special"]:
            _save_document(db_path, area, link["url"], link["kind"], "skipped_special", "หน้านี้เป็นกรณีพิเศษ จึงไม่นำไปรวมกับการคัดเลือกทั่วไป")
            continue
        _apply_notice_dates(db_path, area, notice)
        if not notice["documents"]:
            _save_document(db_path, area, link["url"], link["kind"], "no_file", "พบหน้าประกาศแต่ไม่พบไฟล์ PDF", notice["announced_at"])
            continue
        for document in notice["documents"][:1]:
            _read_notice_file(db_path, area, document["url"], link["kind"], notice["announced_at"], counts)


def _read_notice_file(db_path, area, url: str, kind: str, announced_at: str | None, counts: dict) -> None:
    try:
        status, payload, content_type = get_bytes(url, timeout=20)
    except Exception as exc:
        with _WRITE_LOCK:
            counts["unreadable"] += 1
        _save_document(db_path, area, url, kind, "fetch_failed", f"ดาวน์โหลดไฟล์ไม่ได้: {exc}", announced_at)
        return
    if status != 200 or not payload.startswith(b"%PDF"):
        with _WRITE_LOCK:
            counts["unreadable"] += 1
        _save_document(db_path, area, url, kind, "unreadable", f"ไฟล์ไม่ใช่ PDF ที่อ่านได้ (สถานะ {status}, {content_type})", announced_at)
        return
    facts = interpret_notice_text(extract_pdf_text(payload))
    if not facts["readable"]:
        with _WRITE_LOCK:
            counts["unreadable"] += 1
        _save_document(db_path, area, url, kind, "unreadable", facts["message"], announced_at)
        return
    if facts["openings"] is not None and area["openings"] is not None and facts["openings"] != area["openings"]:
        _save_document(
            db_path,
            area,
            url,
            kind,
            "mismatch",
            f"PDF อ่านได้ {facts['openings']} ตำแหน่ง แต่หน้ารวบรวมเป็น {area['openings']} จึงไม่เขียนทับตัวเลขเดิม",
            announced_at,
        )
        return
    status_name = "confirmed" if facts["openings"] is not None else "no_english_figure"
    _save_document(db_path, area, url, kind, status_name, facts["message"], announced_at)
    if kind == "call_notice" and (facts["appointed_count"] is not None or facts["call_rank_reached"] is not None):
        _save_call_event(db_path, area, url, announced_at, facts)


def _apply_notice_dates(db_path, area, notice: dict) -> None:
    from datetime import date

    status = COVERAGE_ANNOUNCED
    start = notice["application_start"]
    end = notice["application_end"]
    if start and end and start <= date.today().isoformat() <= end:
        status = COVERAGE_OPEN
    evidence = f"หน้ารวบรวมระบุเปิดรับ {area['openings']} ตำแหน่ง ที่ {REPORT_URL}"
    evidence += " | พบหน้าประกาศของเขต"
    if notice["announced_at"]:
        evidence += f" วันที่บนหน้าประกาศ {notice['announced_at']}"
    if start and end:
        evidence += f" ช่วงรับสมัคร {start} ถึง {end}"
    evidence += " ตัวเลขจากไฟล์จะถูกใช้เมื่ออ่านแล้วพบแถวเอกภาษาอังกฤษที่ชัดเจนเท่านั้น"
    with _WRITE_LOCK:
        _apply_notice_dates_unlocked(db_path, area, notice, status, start, end, evidence)


def _apply_notice_dates_unlocked(db_path, area, notice, status, start, end, evidence) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE coverage_records
            SET status = ?, announced_at = COALESCE(?, announced_at),
                application_start = COALESCE(?, application_start),
                application_end = COALESCE(?, application_end),
                source_url = ?, evidence = ?, observed_at = ?
            WHERE source_code = 'kruphuchuay' AND exam_year = 2569 AND area_key = ?
            """,
            (status, notice["announced_at"], start, end, notice["page_url"], evidence, now_iso(), area["area_key"]),
        )


def _save_document(db_path, area, url: str, kind: str, status: str, message: str, announced_at: str | None = None) -> None:
    if not url:
        url = f"missing:{area['area_key']}"
    with _WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO source_documents (
                source_code, area_key, area_name, exam_year, exam_type, subject, doc_kind, url,
                status, message, announced_at, observed_at
            ) VALUES ('area_website', ?, ?, 2569, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                status=excluded.status, message=excluded.message, announced_at=excluded.announced_at,
                observed_at=excluded.observed_at, doc_kind=excluded.doc_kind, area_name=excluded.area_name
            """,
            (
                area["area_key"],
                area["area_name"],
                EXAM_GENERAL,
                SUBJECT,
                kind,
                url,
                status,
                message,
                announced_at,
                now_iso(),
            ),
        )


def _save_call_event(db_path, area, url: str, announced_at: str | None, facts: dict, exam_year: int = 2569) -> None:
    evidence = "ตัวเลขมาจากไฟล์ประกาศที่อ่านได้ และไม่ได้นำจำนวนตำแหน่งเปิดรับมาใช้แทน"
    with _WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO call_events (
                source_code, exam_year, exam_type, subject, area_key, area_name, call_rank_reached,
                appointed_count, remaining_count, announced_at, source_url, evidence, observed_at
            ) VALUES ('area_website', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_code, exam_type, subject, exam_year, area_key, source_url) DO UPDATE SET
                call_rank_reached=excluded.call_rank_reached, appointed_count=excluded.appointed_count,
                remaining_count=excluded.remaining_count, announced_at=excluded.announced_at,
                evidence=excluded.evidence, observed_at=excluded.observed_at
            """,
            (
                exam_year,
                EXAM_GENERAL,
                SUBJECT,
                area["area_key"],
                area["area_name"],
                facts["call_rank_reached"],
                facts["appointed_count"],
                facts["remaining_count"],
                announced_at,
                url,
                evidence,
                now_iso(),
            ),
        )
        ranks = conn.execute(
            """
            SELECT source_url, call_rank_reached FROM call_events
            WHERE area_key = ? AND exam_year = ? AND subject = ? AND exam_type = ? AND call_rank_reached IS NOT NULL
            """,
            (area["area_key"], exam_year, SUBJECT, EXAM_GENERAL),
        ).fetchall()
        distinct = {row["call_rank_reached"] for row in ranks}
        if len(distinct) > 1:
            note = "ตรวจสอบ: แหล่งประกาศระบุลำดับที่เรียกถึงไม่ตรงกัน จึงเก็บทั้งสองแหล่งและยังไม่เลือกค่าใดค่าหนึ่ง"
            for row in ranks:
                conn.execute(
                    "UPDATE call_events SET evidence = ? WHERE area_key = ? AND exam_year = ? AND source_url = ?",
                    (note, area["area_key"], exam_year, row["source_url"]),
                )
            conn.execute(
                "INSERT INTO data_warnings(source_code, area_key, message, created_at, fetch_run_id) VALUES (?, ?, ?, ?, NULL)",
                ("area_website", area["area_key"], f"{area['area_name']} ปี {exam_year}: {note}", now_iso()),
            )


def collect_missing_evidence(db_path) -> dict:
    """Look up unpublished applicant counts, older call notices, and the waiting list once."""
    return {
        "waiting": _recheck_waiting_list(db_path),
        "applicants": _search_notice_pages_for_applicants(db_path),
        "calls": _search_past_call_notices(db_path),
    }


def _recheck_waiting_list(db_path) -> str:
    started = now_iso()
    try:
        status, html = get_text(WAITING_URL, timeout=20)
    except Exception as exc:
        message = f"เปิดรายชื่อรอประกาศไม่ได้: {exc}"
        _finish_run(db_path, "coverage_waiting", "failed", None, message, 0, None, False, started)
        return message
    names, _reference_at, ok = parse_area_name_list(html)
    if status != 200 or not ok:
        message = "โครงสร้างหน้ารอประกาศเปลี่ยนหรือเปิดไม่ได้ จึงไม่แก้สถานะเขตที่บันทึกไว้"
        _finish_run(db_path, "coverage_waiting", "structure_changed", status, message, 0, None, False, started)
        return message
    current = set(names)
    with connect(db_path) as conn:
        stored = {row["area_name"] for row in conn.execute("SELECT area_name FROM coverage_records WHERE status = 'รอประกาศ'")}
        known = {row["area_name"] for row in conn.execute("SELECT area_name FROM coverage_records")}
    if current == stored:
        message = f"รายชื่อรอประกาศ {len(current)} เขตไม่เปลี่ยนจากข้อมูลที่บันทึกไว้ จึงไม่แก้สถานะ และไม่ได้ตีความว่าเขตเหล่านี้ไม่เปิดสอบ"
        _finish_run(db_path, "coverage_waiting", "success", status, message, 0, None, True, started)
        return message
    added = sorted(current - known)
    left = sorted(stored - current)
    with connect(db_path) as conn:
        for name in left:
            conn.execute(
                "INSERT INTO data_warnings(source_code, area_key, message, created_at, fetch_run_id) VALUES (?, '', ?, ?, NULL)",
                (
                    "kruphuchuay",
                    f"{name} ไม่อยู่ในรายชื่อรอประกาศรอบนี้แล้ว แต่ยังไม่พบแถวเอกภาษาอังกฤษ จึงไม่เปลี่ยนสถานะเป็นไม่เปิดสอบ",
                    now_iso(),
                ),
            )
        for name in added:
            conn.execute(
                "INSERT INTO data_warnings(source_code, area_key, message, created_at, fetch_run_id) VALUES (?, '', ?, ?, NULL)",
                (
                    "kruphuchuay",
                    f"พบ {name} ในรายชื่อรอประกาศ แต่ยังไม่มีรหัสเขตจากหน้ารายวิชา จึงยังไม่สร้างแถวสอบ",
                    now_iso(),
                ),
            )
    message = f"รายชื่อรอประกาศเปลี่ยน เพิ่ม {len(added)} เขต หลุดจากรายชื่อ {len(left)} เขต โดยยังไม่สรุปว่าไม่เปิดสอบ"
    _finish_run(db_path, "coverage_waiting", "success", status, message, len(added) + len(left), None, True, started)
    return message


def _search_notice_pages_for_applicants(db_path) -> str:
    with connect(db_path) as conn:
        pages = conn.execute(
            """
            SELECT DISTINCT area_key, area_name, url
            FROM source_documents
            WHERE exam_year = 2569 AND doc_kind = 'recruitment_notice' AND url LIKE 'http%'
            """
        ).fetchall()
    found = 0
    checked = 0
    for page in pages:
        area = dict(page)
        try:
            status, html = get_text(area["url"], timeout=15)
        except Exception as exc:
            _save_document(db_path, area, area["url"], "recruitment_notice", "fetch_failed", f"เปิดหน้าประกาศซ้ำไม่ได้: {exc}")
            continue
        checked += 1
        if status != 200:
            _save_document(db_path, area, area["url"], "recruitment_notice", "fetch_failed", f"หน้าประกาศตอบสถานะ {status}")
            continue
        facts = interpret_notice_text(visible_notice_text(html))
        if facts["applicants"] is None:
            _save_document(
                db_path,
                area,
                area["url"],
                "recruitment_notice",
                "applicants_not_found",
                "ไม่พบจำนวนผู้สมัครเอกภาษาอังกฤษบนข้อความของหน้าประกาศ จึงไม่ประมาณจำนวน",
            )
            continue
        if _store_found_applicants(db_path, area, facts["applicants"], area["url"]):
            found += 1
            _save_document(
                db_path,
                area,
                area["url"],
                "recruitment_notice",
                "confirmed",
                f"พบผู้สมัครเอกภาษาอังกฤษ {facts['applicants']} คนบนข้อความของหน้าประกาศ",
            )
    return f"ตรวจข้อความหน้าประกาศ {checked} หน้า พบจำนวนผู้สมัคร {found} เขต"


def _store_found_applicants(db_path, area, applicants: int, source_url: str) -> bool:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT applicants, applicants_known FROM recruitment_records
            WHERE source_code = 'kruphuchuay' AND exam_year = 2569 AND area_key = ? AND subject = ?
            """,
            (area["area_key"], SUBJECT),
        ).fetchone()
        if row is None:
            return False
        if row["applicants_known"] and row["applicants"] != applicants:
            conn.execute(
                "INSERT INTO data_warnings(source_code, area_key, message, created_at, fetch_run_id) VALUES (?, ?, ?, ?, NULL)",
                (
                    "area_website",
                    area["area_key"],
                    f"{area['area_name']} จำนวนผู้สมัครที่บันทึกไว้คือ {row['applicants']} แต่หน้า {source_url} ระบุ {applicants} จึงเก็บของเดิมและให้ตรวจสอบ",
                    now_iso(),
                ),
            )
            return False
        if row["applicants_known"]:
            return False
        conn.execute(
            """
            UPDATE recruitment_records
            SET applicants = ?, applicants_known = 1
            WHERE source_code = 'kruphuchuay' AND exam_year = 2569 AND area_key = ? AND subject = ?
            """,
            (applicants, area["area_key"], SUBJECT),
        )
        conn.execute(
            """
            INSERT INTO record_changes(source_code, area_key, area_name, exam_year, field_name, old_value, new_value, changed_at, fetch_run_id)
            VALUES ('area_website', ?, ?, 2569, 'applicants', '', ?, ?, NULL)
            """,
            (area["area_key"], area["area_name"], str(applicants), now_iso()),
        )
    return True


def _search_past_call_notices(db_path) -> str:
    with connect(db_path) as conn:
        areas = conn.execute(
            """
            SELECT DISTINCT r.area_key, r.area_name, r.area_website, r.openings
            FROM recruitment_records r
            WHERE r.exam_year = 2569 AND r.opened = 1 AND r.subject = ?
              AND r.area_website IS NOT NULL AND r.area_website != ''
              AND EXISTS (
                SELECT 1 FROM recruitment_records h
                WHERE h.area_key = r.area_key AND h.listed_count > 0
                  AND h.appointed_count BETWEEN 0 AND h.listed_count
              )
            """,
            (SUBJECT,),
        ).fetchall()
    checked = 0

    def visit(row):
        nonlocal checked
        area = dict(row)
        try:
            status, html = get_text(area["area_website"], timeout=12)
        except Exception as exc:
            _save_document(db_path, area, f"{area['area_website']}#past-call", "call_notice", "fetch_failed", f"เปิดเว็บเขตเพื่อหาประกาศเรียกบรรจุย้อนหลังไม่ได้: {exc}")
            return
        with _WRITE_LOCK:
            checked += 1
        if status != 200:
            _save_document(db_path, area, f"{area['area_website']}#past-call", "call_notice", "fetch_failed", f"เว็บเขตตอบสถานะ {status}")
            return
        links = find_past_call_links(html, area["area_website"])
        if not links:
            _save_document(
                db_path,
                area,
                f"{area['area_website']}#past-call",
                "call_notice",
                "not_found",
                "ไม่พบลิงก์ประกาศเรียกบรรจุครูผู้ช่วยปี 2566-2568 ที่ระบุปีบนหน้าแรก",
            )
            return
        for link in links:
            _read_past_call(db_path, area, link)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(visit, [dict(row) for row in areas]))
    with connect(db_path) as conn:
        saved = conn.execute("SELECT COUNT(*) AS n FROM call_events").fetchone()["n"]
    return f"ตรวจเว็บเขตที่มีประวัติเรียกบรรจุ {checked} เขต เก็บเหตุการณ์เรียกบรรจุที่อ่านตัวเลขได้ {saved} รายการ"

def _read_past_call(db_path, area, link: dict) -> None:
    try:
        status, html = get_text(link["url"], timeout=12)
    except Exception as exc:
        _save_document(db_path, area, link["url"], "call_notice", "fetch_failed", f"เปิดหน้าประกาศเรียกบรรจุไม่ได้: {exc}")
        return
    if status != 200:
        _save_document(db_path, area, link["url"], "call_notice", "fetch_failed", f"หน้าประกาศเรียกบรรจุตอบสถานะ {status}")
        return
    notice = parse_notice_page(html, link["url"])
    facts = interpret_notice_text(visible_notice_text(html))
    if facts["appointed_count"] is None and facts["call_rank_reached"] is None and notice["documents"]:
        document = notice["documents"][0]
        try:
            file_status, payload, _content_type = get_bytes(document["url"], timeout=20)
        except Exception as exc:
            _save_document(db_path, area, document["url"], "call_notice", "unreadable", f"ดาวน์โหลดไฟล์เรียกบรรจุไม่ได้: {exc}", notice["announced_at"])
            return
        if file_status == 200 and payload.startswith(b"%PDF"):
            facts = interpret_notice_text(extract_pdf_text(payload))
            target = document["url"]
        else:
            _save_document(db_path, area, document["url"], "call_notice", "unreadable", "ไฟล์ประกาศเรียกบรรจุไม่ใช่ PDF ที่อ่านได้", notice["announced_at"])
            return
    else:
        target = link["url"]
    if not facts["readable"]:
        _save_document(db_path, area, target, "call_notice", "unreadable", facts["message"], notice["announced_at"])
        return
    if facts["appointed_count"] is None and facts["call_rank_reached"] is None:
        _save_document(db_path, area, target, "call_notice", "not_found", "อ่านหน้าประกาศได้แต่ไม่พบจำนวนเรียกบรรจุหรือลำดับที่เรียกถึงของเอกภาษาอังกฤษ", notice["announced_at"])
        return
    _save_document(db_path, area, target, "call_notice", "confirmed", facts["message"], notice["announced_at"])
    _save_call_event(db_path, area, target, notice["announced_at"], facts, link["exam_year"])


def refresh_hrms(db_path, http_get) -> dict:
    started = now_iso()
    status, page = http_get(HRMS_PAGE)
    if status != 200:
        _finish_run(db_path, "hrms_obec", "failed", status, f"หน้า HRMS ตอบสถานะ {status}", 0, None, False, started)
        return {"source": "hrms_obec", "status": "failed", "message": f"สถานะ {status}"}
    runtime_match = None
    import re

    found = re.search(r"(/_nuxt/runtime\.[a-f0-9]+\.js)", page)
    if not found:
        _finish_run(db_path, "hrms_obec", "structure_changed", status, "ไม่พบไฟล์ runtime ของหน้า HRMS", 0, None, False, started)
        return {"source": "hrms_obec", "status": "structure_changed", "message": "ไม่พบไฟล์ runtime ของหน้า HRMS"}
    runtime_status, runtime = http_get("https://hrms.obec.go.th" + found.group(1))
    if runtime_status != 200:
        _finish_run(db_path, "hrms_obec", "failed", runtime_status, "โหลดไฟล์ runtime ของ HRMS ไม่สำเร็จ", 0, None, False, started)
        return {"source": "hrms_obec", "status": "failed", "message": "โหลด runtime ไม่สำเร็จ"}
    script_url = discover_report_script(page, runtime)
    if not script_url:
        _finish_run(db_path, "hrms_obec", "structure_changed", status, "โครงสร้างไฟล์ HRMS เปลี่ยน", 0, None, False, started)
        return {"source": "hrms_obec", "status": "structure_changed", "message": "โครงสร้างไฟล์ HRMS เปลี่ยน"}
    script_status, script = http_get(script_url)
    parsed = parse_english_staffing(script) if script_status == 200 else None
    if not parsed:
        _finish_run(db_path, "hrms_obec", "structure_changed", script_status, "ไม่พบชุดข้อมูลภาษาอังกฤษในหน้า HRMS", 0, None, False, started)
        return {"source": "hrms_obec", "status": "structure_changed", "message": "ไม่พบชุดข้อมูลภาษาอังกฤษในหน้า HRMS"}
    run_id = _start_run(db_path, "hrms_obec", started)
    _save_staffing(
        db_path,
        "hrms_obec",
        run_id,
        {
            **parsed,
            "source_url": HRMS_PAGE,
            "reference_at": None,
        },
    )
    message = (
        f"ภาษาอังกฤษทั้งประเทศ มีครู {parsed['actual_count']:,} จากเกณฑ์ {parsed['standard_count']:,} "
        f"ผลต่าง {parsed['gap_count']:+,} เกษียณสะสม 10 ปี {parsed['retire_10y']:,} ความต้องการสะสม {parsed['need_10y']:,} "
        "ตัวเลขนี้ไม่ใช่ตำแหน่งสอบ"
    )
    _complete_run(db_path, run_id, "success", script_status, message, 1, hashlib.sha256(script.encode("utf-8", "replace")).hexdigest(), True)
    return {"source": "hrms_obec", "status": "success", "message": message, "records": 1}


def refresh_notice_sources(db_path) -> dict:
    """Read official notice pages. Numbers are stored only when district, subject, year, and type agree."""
    started = now_iso()
    saved_docs = 0
    saved_facts = 0
    failed = 0
    review = 0
    notes = []
    areas = _area_index(db_path)
    grouped: dict[str, list] = {}
    for source in NOTICE_SOURCES:
        grouped.setdefault(source["code"], []).append(source)
    for code, pages in grouped.items():
        found = 0
        page_status = None
        page_failed = False
        source_problem = False
        for source in pages:
            try:
                status, html = get_text(source["url"], timeout=25)
            except Exception as exc:
                failed += 1
                page_failed = True
                notes.append(f"{source['name']}: เปิดหน้าไม่ได้ ({exc})")
                continue
            if status != 200:
                failed += 1
                page_failed = True
                notes.append(f"{source['name']}: สถานะ {status}")
                continue
            page_status = status
            notices = parse_notice_listing(html, source["url"])
            found += len(notices)
            for notice in notices:
                if _save_source_notice(db_path, source["code"], notice, "listed", _notice_message(notice)):
                    saved_docs += 1
                if notice["doc_kind"] == "pdf":
                    outcome = _read_official_pdf(db_path, source["code"], notice, areas)
                elif notice["exam_type"] == EXAM_NEWS or "795" in notice["title"]:
                    outcome = "news"
                else:
                    outcome = _follow_article(db_path, source["code"], notice, areas)
                saved_facts += outcome.get("facts", 0) if isinstance(outcome, dict) else 0
                review += outcome.get("review", 0) if isinstance(outcome, dict) else 0
                outcome_failed = outcome.get("failed", 0) if isinstance(outcome, dict) else 0
                failed += outcome_failed
                if outcome_failed:
                    source_problem = True
        _finish_run(
            db_path,
            code,
            "failed" if page_failed and page_status is None else ("partial" if page_failed or source_problem else "success"),
            page_status,
            f"พบประกาศครูผู้ช่วย {found} รายการ" if page_status else "เปิดหน้าไม่ได้",
            found,
            None,
            page_status is not None,
            started,
        )
    message = f"เอกสารใหม่หรือที่ตรวจซ้ำ {saved_docs} รายการ ตัวเลขเอกภาษาอังกฤษที่บันทึก {saved_facts} แถว รอตรวจ {review} เปิดไม่ได้ {failed}"
    if notes:
        message += " | " + " ; ".join(notes[:4])
    return {"source": "notice_sources", "status": "partial" if failed or review else "success", "message": message, "records": saved_facts}


def _notice_message(notice: dict) -> str:
    if "795" in (notice.get("title") or ""):
        return "ข่าวระบุ 795 อัตรารอบทั่วไปทั้งประเทศทุกวิชา ไม่ใช่จำนวนตำแหน่งเอกภาษาอังกฤษรายเขต จึงไม่นำไปคิดคะแนน"
    return f"ประเภท {notice['exam_type']} ยังไม่ใช้ตัวเลขจากชื่อเรื่องอย่างเดียว"


def _catalog_index(db_path) -> dict[str, tuple[str, str]]:
    """Area catalog already stored from the source list. Recruitment keys win over a name-only key."""
    with connect(db_path) as conn:
        coverage = conn.execute(
            """
            SELECT area_key, area_name FROM coverage_records
            WHERE subject = ? AND area_key IS NOT NULL AND area_key != ''
            """,
            (SUBJECT,),
        ).fetchall()
        recruitment = conn.execute(
            "SELECT area_key, area_name FROM recruitment_records WHERE subject = ? AND area_key != ''",
            (SUBJECT,),
        ).fetchall()
    index: dict[str, tuple[str, str]] = {}
    blocked = set()
    for row in list(coverage) + list(recruitment):
        key = norm_area(row["area_name"])
        current = index.get(key)
        incoming = (row["area_key"], row["area_name"])
        if current is None:
            index[key] = incoming
            continue
        if current[0] == incoming[0]:
            if not str(incoming[0]).startswith("name:"):
                index[key] = incoming
            continue
        if str(current[0]).startswith("name:") and not str(incoming[0]).startswith("name:"):
            index[key] = incoming
            continue
        if str(incoming[0]).startswith("name:") and not str(current[0]).startswith("name:"):
            continue
        blocked.add(key)
    for key in blocked:
        index.pop(key, None)
    return index


def _area_index(db_path) -> dict[str, tuple[str, str]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT area_key, area_name FROM recruitment_records WHERE subject = ?",
            (SUBJECT,),
        ).fetchall()
    return {norm_area(row["area_name"]): (row["area_key"], row["area_name"]) for row in rows}


def _save_source_notice(db_path, source: str, notice: dict, status: str, message: str) -> bool:
    with connect(db_path) as conn:
        existed = conn.execute("SELECT id FROM source_documents WHERE url = ?", (notice["url"],)).fetchone()
        conn.execute(
            """
            INSERT INTO source_documents (
                source_code, area_key, area_name, exam_year, exam_type, subject, doc_kind, url,
                status, message, announced_at, observed_at
            ) VALUES (?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                source_code=excluded.source_code, exam_year=excluded.exam_year, exam_type=excluded.exam_type,
                status=excluded.status, message=excluded.message, announced_at=COALESCE(excluded.announced_at, source_documents.announced_at),
                observed_at=excluded.observed_at, doc_kind=excluded.doc_kind
            """,
            (
                source,
                notice.get("exam_year"),
                notice.get("exam_type"),
                SUBJECT,
                notice.get("doc_kind") or "article",
                notice["url"],
                status,
                message[:500],
                notice.get("announced_at"),
                now_iso(),
            ),
        )
    return existed is None


def _follow_article(db_path, source: str, notice: dict, areas: dict) -> dict:
    try:
        status, html = get_text(notice["url"], timeout=20)
    except Exception as exc:
        _save_source_notice(db_path, source, notice, "fetch_failed", f"เปิดหน้าประกาศไม่ได้: {exc}")
        return {"failed": 1}
    if status != 200:
        _save_source_notice(db_path, source, notice, "fetch_failed", f"หน้าประกาศตอบสถานะ {status}")
        return {"failed": 1}
    pdfs = pdf_links(html, notice["url"])
    totals = {"facts": 0, "review": 0, "failed": 0}
    for pdf in pdfs[:2]:
        pdf["exam_type"] = pdf["exam_type"] if pdf["exam_type"] != EXAM_NEWS else notice["exam_type"]
        pdf["exam_year"] = pdf["exam_year"] or notice.get("exam_year")
        outcome = _read_official_pdf(db_path, source, pdf, areas)
        for key in totals:
            totals[key] += outcome.get(key, 0)
    if not pdfs:
        kind = classify_exam_type(notice["title"])
        _save_source_notice(db_path, source, notice, "no_file", f"หน้าข่าวประเภท {kind} ไม่มีไฟล์ PDF จึงไม่บันทึกตัวเลขจากชื่อเรื่อง")
    else:
        _save_source_notice(db_path, source, notice, "listed", f"พบไฟล์แนบ {len(pdfs)} ไฟล์ ตัวเลขจะถูกบันทึกเฉพาะเมื่อยืนยันเขต วิชา ปี และประเภทการสอบได้")
    return totals


def _read_official_pdf(db_path, source: str, notice: dict, areas: dict) -> dict:
    try:
        status, payload, _content_type = get_bytes(notice["url"], timeout=40)
    except Exception as exc:
        _save_source_notice(db_path, source, notice, "fetch_failed", f"ดาวน์โหลด PDF ไม่ได้: {exc}")
        return {"failed": 1}
    if status != 200 or not payload.startswith(b"%PDF"):
        _save_source_notice(db_path, source, notice, "unreadable", f"ไฟล์ไม่ใช่ PDF ที่เปิดได้ (สถานะ {status})")
        return {"review": 1}
    text = extract_pdf_text(payload)
    if extraction_status(text) == "review":
        _save_source_notice(
            db_path,
            source,
            notice,
            "review",
            "ดาวน์โหลดได้แต่ถอดอักษรไทยจาก PDF ไม่ได้ และไม่ได้ใช้ OCR สร้างตัวเลขแทน จึงยังไม่บันทึกจำนวน",
        )
        return {"review": 1}
    if notice.get("exam_type") == EXAM_EXISTING_LIST or "บัญชีคงเหลือ" in notice.get("title", "") or "บัญชีคงเหลือ" in notice["url"]:
        return _store_account_table(db_path, source, notice, text, areas)
    _save_source_notice(
        db_path,
        source,
        notice,
        "no_english_figure",
        "อ่านข้อความได้ แต่ไฟล์นี้ไม่ใช่ตารางบัญชีคงเหลือรายเขต จึงไม่ดึงจำนวนตำแหน่งหรือผู้สมัครจากไฟล์นี้",
    )
    return {}


def import_local_account_pdf(db_path, path: str, year: int) -> dict:
    """Store English balance rows from a local สพฐ file. Does not touch recruitment_records."""
    from pathlib import Path

    from src.connectors.gazette import account_consistent, match_area_name, parse_english_account_pdf

    file_path = Path(path)
    url = file_path.resolve().as_uri()
    notice = {
        "url": url,
        "exam_year": year,
        "exam_type": EXAM_EXISTING_LIST,
        "doc_kind": "account_balance",
        "announced_at": None,
    }
    if year not in {2566, 2567, 2568} or not file_path.is_file():
        _save_source_notice(db_path, "local_account_pdf", notice, "review", "ไฟล์หรือปีไม่ตรงกับบัญชีคงเหลือ 2566-2568 จึงไม่บันทึกตัวเลข")
        return {"stored": 0, "unmatched": 0, "inconsistent": 0, "review": 1}
    areas = _catalog_index(db_path)
    rows = parse_english_account_pdf(file_path.read_bytes())
    stored = 0
    unmatched = 0
    inconsistent = 0
    for row in rows:
        if not account_consistent(row.get("listed"), row.get("appointed"), row.get("remaining")):
            inconsistent += 1
            continue
        matched = match_area_name(areas, row["area_name"])
        if matched is None:
            unmatched += 1
            continue
        area_key, area_name = matched
        _save_account_event(db_path, "local_account_pdf", area_key, area_name, year, url, row)
        stored += 1
    message = (
        f"บัญชีคงเหลือเอกภาษาอังกฤษปี {year} จากไฟล์ท้องถิ่น บันทึก {stored} เขต "
        f"ชื่อไม่ตรงแบบหนึ่งต่อหนึ่ง {unmatched} แถว ตัวเลขไม่ลงกันหรือช่องว่าง {inconsistent} แถว "
        "ไม่ใช่ตำแหน่งสอบปี 2569 และไม่ใช่ลำดับบัญชีที่เรียกถึง"
    )
    _save_source_notice(db_path, "local_account_pdf", notice, "confirmed" if stored else "review", message)
    return {"stored": stored, "unmatched": unmatched, "inconsistent": inconsistent, "review": 0 if stored else 1}


def note_local_file(db_path, url: str, year: int | None, kind: str, status: str, message: str) -> None:
    _save_source_notice(
        db_path,
        "local_account_pdf",
        {"url": url, "exam_year": year, "exam_type": EXAM_NEWS, "doc_kind": kind, "announced_at": None},
        status,
        message,
    )


def _store_account_table(db_path, source: str, notice: dict, text: str, areas: dict) -> dict:
    rows = parse_english_account_table(text)
    year = notice.get("exam_year")
    if year is None or not rows:
        _save_source_notice(db_path, source, notice, "review", "อ่าน PDF ได้แต่ไม่พบตารางเอกภาษาอังกฤษที่แยกเขตและปีได้ จึงไม่บันทึกตัวเลข")
        return {"review": 1}
    stored = 0
    unmatched = 0
    conflict = 0
    for row in rows:
        if not row["consistent"]:
            conflict += 1
            continue
        matched = areas.get(norm_area(row["area_name"]))
        if matched is None:
            unmatched += 1
            continue
        area_key, area_name = matched
        _save_account_event(db_path, source, area_key, area_name, year, notice["url"], row)
        stored += 1
    message = (
        f"บัญชีคงเหลือเอกภาษาอังกฤษปี {year} บันทึก {stored} เขตที่ชื่อตรงกับตารางสอบ "
        f"ไม่ตรงชื่อ {unmatched} เขต แถวที่ตัวเลขขึ้นบัญชี-เรียกบรรจุ-คงเหลือไม่ลงกัน {conflict} แถว "
        "ตัวเลขนี้ไม่ใช่ตำแหน่งสอบปี 2569 และไม่ใช่ลำดับบัญชีที่เรียกถึง"
    )
    _save_source_notice(db_path, source, notice, "confirmed" if stored else "review", message)
    return {"facts": stored, "review": 0 if stored else 1}


def _save_account_event(db_path, source: str, area_key: str, area_name: str, year: int, url: str, row: dict) -> None:
    evidence = "คอลัมน์ภาษาอังกฤษจากบัญชีคงเหลือที่เขตรายงาน สพฐ. " + row["snippet"]
    with connect(db_path) as conn:
        existing = conn.execute(
            """
            SELECT appointed_count FROM recruitment_records
            WHERE area_key = ? AND exam_year = ? AND subject = ? AND exam_type = ? AND applicants_known IS NOT NULL
            """,
            (area_key, year, SUBJECT, EXAM_GENERAL),
        ).fetchone()
        if existing and existing["appointed_count"] not in (None, row["appointed"]):
            warning = (
                f"{area_name} ปี {year} หน้ารวบรวมเรียกบรรจุ {existing['appointed_count']} "
                f"แต่บัญชีคงเหลือระบุ {row['appointed']} จึงเก็บทั้งสองแหล่งและไม่เขียนทับตารางสอบ"
            )
            seen = conn.execute(
                "SELECT 1 FROM data_warnings WHERE source_code = ? AND area_key = ? AND message = ?",
                (source, area_key, warning),
            ).fetchone()
            if seen is None:
                conn.execute(
                    "INSERT INTO data_warnings(source_code, area_key, message, created_at, fetch_run_id) VALUES (?, ?, ?, ?, NULL)",
                    (source, area_key, warning, now_iso()),
                )
        conn.execute(
            """
            INSERT INTO call_events (
                source_code, exam_year, exam_type, subject, area_key, area_name, call_rank_reached,
                appointed_count, remaining_count, announced_at, source_url, evidence, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(source_code, exam_type, subject, exam_year, area_key, source_url) DO UPDATE SET
                appointed_count=excluded.appointed_count, remaining_count=excluded.remaining_count,
                evidence=excluded.evidence, observed_at=excluded.observed_at
            """,
            (
                source,
                year,
                EXAM_EXISTING_LIST,
                SUBJECT,
                area_key,
                area_name,
                row["appointed"],
                row["remaining"],
                url,
                evidence[:800],
                now_iso(),
            ),
        )


def refresh_officials(db_path, http_get) -> list[dict]:
    results = []
    for site in OFFICIAL_SITES:
        started = now_iso()
        try:
            status, html = http_get(site["url"])
        except Exception as exc:
            _finish_run(db_path, site["code"], "failed", None, f"เข้าเว็บไม่ได้: {exc}", 0, None, False, started)
            results.append({"source": site["code"], "status": "failed", "message": str(exc)})
            continue
        if status != 200:
            _finish_run(db_path, site["code"], "failed", status, f"เว็บตอบสถานะ {status}", 0, None, False, started)
            results.append({"source": site["code"], "status": "failed", "message": f"สถานะ {status}"})
            continue
        message = classify_homepage(html)
        _finish_run(db_path, site["code"], "no_structured_data", status, message, 0, None, False, started)
        results.append({"source": site["code"], "status": "no_structured_data", "message": message})
    return results


def import_records(db_path, records: list[dict]) -> dict:
    init_db(db_path)
    if not records:
        return {"status": "failed", "message": "ไม่มีแถวที่นำเข้าได้", "records": 0}
    run_id = _start_run(db_path, "csv_import", now_iso())
    written = _save_recruitment(db_path, "csv_import", run_id, records, replace_missing=False)
    _complete_run(db_path, run_id, "success", None, f"นำเข้า {written} แถวจากไฟล์", written, None, True)
    return {"status": "success", "message": f"นำเข้า {written} แถว", "records": written}


def should_refresh(db_path, now: datetime | None = None) -> bool:
    init_db(db_path)
    with connect(db_path) as conn:
        hours = float(get_pref(conn, "refresh_hours", "6") or 6)
        row = conn.execute(
            """
            SELECT finished_at, status FROM fetch_runs
            WHERE source_code = 'kruphuchuay' AND status IN ('success', 'partial')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    if row is None:
        return True
    return classify_data_status(row["finished_at"], "success", hours, now) != STATUS_FRESH


def load_dashboard(db_path, focus_year: int | None = None, org_level: str = "ทั้งหมด", query: str = "", favorites_only: bool = False, data_filter: str = "ทั้งหมด", coverage_status: str = "ทั้งหมด") -> dict:
    init_db(db_path)
    with connect(db_path) as conn:
        hours = float(get_pref(conn, "refresh_hours", "6") or 6)
        favorites = json.loads(get_pref(conn, "favorite_area_keys", "[]") or "[]")
        rows = conn.execute(
            """
            SELECT * FROM recruitment_records
            WHERE source_code = 'kruphuchuay' AND subject = ? AND exam_type = ?
            ORDER BY area_name, exam_year
            """,
            (SUBJECT, EXAM_GENERAL),
        ).fetchall()
        link_staffing_records(conn)
        staffing_rows = conn.execute(
            "SELECT * FROM staffing_records WHERE source_code = 'kruphuchuay' AND scope = 'area' AND subject = ?",
            (SUBJECT,),
        ).fetchall()
        weight_raw = get_pref(conn, "score_weights", "")
        try:
            stored_weights = json.loads(weight_raw) if weight_raw else None
        except json.JSONDecodeError:
            stored_weights = None
        weights = normalize_weights(stored_weights)
        national = conn.execute(
            "SELECT * FROM staffing_records WHERE source_code = 'hrms_obec' AND scope = 'national' AND subject = ? ORDER BY id DESC LIMIT 1",
            (SUBJECT,),
        ).fetchone()
        summaries = conn.execute(
            "SELECT * FROM subject_summaries WHERE source_code = 'kruphuchuay' AND subject = ? AND exam_type = ? ORDER BY exam_year",
            (SUBJECT, EXAM_GENERAL),
        ).fetchall()
        runs = conn.execute("SELECT * FROM fetch_runs ORDER BY id DESC LIMIT 12").fetchall()
        changes = conn.execute("SELECT * FROM record_changes ORDER BY id DESC LIMIT 20").fetchall()
        warnings = conn.execute("SELECT * FROM data_warnings ORDER BY id DESC LIMIT 20").fetchall()
        coverage_rows = conn.execute(
            """
            SELECT * FROM coverage_records
            WHERE source_code = 'kruphuchuay' AND subject = ? AND exam_type = ? AND exam_year = 2569
            ORDER BY area_name
            """,
            (SUBJECT, EXAM_GENERAL),
        ).fetchall()
        documents = conn.execute(
            "SELECT * FROM source_documents WHERE subject = ? AND exam_year = 2569 ORDER BY area_name, id DESC",
            (SUBJECT,),
        ).fetchall()
        call_events = conn.execute(
            "SELECT * FROM call_events WHERE subject = ? AND exam_type = ? ORDER BY area_name, exam_year",
            (SUBJECT, EXAM_GENERAL),
        ).fetchall()
        account_events = conn.execute(
            """
            SELECT * FROM call_events
            WHERE subject = ? AND exam_type = ?
            ORDER BY area_name, exam_year
            """,
            (SUBJECT, EXAM_EXISTING_LIST),
        ).fetchall()
        monitor_documents = conn.execute(
            """
            SELECT * FROM source_documents
            WHERE source_code IN ('personnel_eoffice', 'obec_assistant', 'prd_teacher', 'otepc_circular')
            ORDER BY observed_at DESC
            """
        ).fetchall()
        latest_sources = conn.execute(
            """
            SELECT source_code, finished_at, status, message, records_written
            FROM fetch_runs
            WHERE id IN (SELECT MAX(id) FROM fetch_runs GROUP BY source_code)
            ORDER BY source_code
            """
        ).fetchall()
        success_sources = conn.execute(
            """
            SELECT source_code, finished_at
            FROM fetch_runs
            WHERE status IN ('success', 'partial')
              AND id IN (
                SELECT MAX(id) FROM fetch_runs
                WHERE status IN ('success', 'partial')
                GROUP BY source_code
              )
            """
        ).fetchall()
        main_run = conn.execute(
            """
            SELECT * FROM fetch_runs
            WHERE source_code = 'kruphuchuay' AND status IN ('success', 'partial')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        latest_run = conn.execute(
            "SELECT * FROM fetch_runs WHERE source_code = 'kruphuchuay' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    coverage = [dict(row) for row in coverage_rows]
    notices = _best_notices(documents)
    areas = _bundle(rows, staffing_rows, {row["area_key"]: row for row in coverage}, notices)
    years = sorted({year for area in areas for year in area.years})
    selected = focus_year if focus_year in years else (max(years) if years else None)
    filtered = []
    for area in areas:
        if org_level != "ทั้งหมด" and area.org_level != org_level:
            continue
        if query and query.strip() not in area.area_name:
            continue
        if favorites_only and area.area_key not in favorites:
            continue
        stat = area.years.get(selected) if selected else None
        if data_filter == "เปิดสอบ" and not (stat and stat.opened):
            continue
        if data_filter == "ไม่เปิดสอบ" and not (stat and stat.opened is False):
            continue
        if data_filter == "มีจำนวนผู้สมัคร" and not (stat and stat.applicants_known):
            continue
        if data_filter == "ยังไม่มีจำนวนผู้สมัคร" and not (stat and stat.opened and not stat.applicants_known):
            continue
        if coverage_status != "ทั้งหมด" and area.coverage_status != coverage_status:
            continue
        filtered.append(area)
    visible_coverage = []
    for item in coverage:
        if org_level != "ทั้งหมด" and not item["area_name"].startswith(org_level):
            continue
        if query and query.strip() not in item["area_name"]:
            continue
        if coverage_status != "ทั้งหมด" and item["status"] != coverage_status:
            continue
        visible_coverage.append(item)
    failed_sources = [
        dict(row)
        for row in latest_sources
        if row["status"] not in {"success", "partial", "running"}
    ]
    status_name = classify_data_status(
        main_run["finished_at"] if main_run else None,
        latest_run["status"] if latest_run else None,
        hours,
    )
    return {
        "status": status_name,
        "interval_hours": hours,
        "favorites": favorites,
        "years": years,
        "focus_year": selected,
        "areas": filtered,
        "all_areas": areas,
        "summaries": [dict(row) for row in summaries],
        "national_staffing": dict(national) if national else None,
        "runs": [dict(row) for row in runs],
        "changes": [dict(row) for row in changes],
        "warnings": [dict(row) for row in warnings],
        "last_success": main_run["finished_at"] if main_run else None,
        "last_attempt": latest_run["finished_at"] if latest_run else None,
        "last_message": latest_run["message"] if latest_run else None,
        "reference_at": next((row["reference_at"] for row in rows if row["reference_at"]), None),
        "coverage": visible_coverage,
        "coverage_all": coverage,
        "documents": [dict(row) for row in documents],
        "call_events": [dict(row) for row in call_events],
        "account_events": [dict(row) for row in account_events],
        "monitor_documents": [dict(row) for row in monitor_documents],
        "failed_sources": failed_sources,
        "source_runs": [dict(row) for row in latest_sources],
        "source_success": {row["source_code"]: row["finished_at"] for row in success_sources},
        "weights": weights,
        "staffing_rows": [dict(row) for row in staffing_rows],
    }


def _best_notices(documents) -> dict[str, dict]:
    rank = {"confirmed": 0, "mismatch": 1, "unreadable": 2, "no_file": 3, "no_english_figure": 4, "no_notice": 5, "fetch_failed": 6}
    chosen: dict[str, dict] = {}
    for row in documents:
        key = row["area_key"]
        if not key or row["doc_kind"] == "area_homepage" and row["status"] == "no_notice":
            current = chosen.get(key)
            if current is None:
                chosen[key] = row
            continue
        current = chosen.get(key)
        if current is None or rank.get(row["status"], 9) < rank.get(current["status"], 9):
            chosen[key] = row
    return {key: row for key, row in chosen.items() if row["status"] in {"confirmed", "mismatch", "unreadable", "no_file", "no_english_figure"}}


def _match_staffing(area_key: str, area_name: str, by_key: dict, by_name: dict):
    matched = by_key.get(area_key)
    if matched is not None:
        return matched
    named = by_name.get(area_name) or []
    if len(named) != 1:
        return None
    only = named[0]
    if only["area_key"] not in (None, "", area_key):
        return None
    return only


def _bundle(rows, staffing_rows, coverage=None, notices=None) -> list[AreaInput]:
    by_key = {}
    by_name: dict[str, list] = {}
    for item in staffing_rows:
        if item["area_key"]:
            by_key[item["area_key"]] = item
        by_name.setdefault(item["area_name"], []).append(item)
    grouped: dict[str, AreaInput] = {}
    for row in rows:
        area = grouped.get(row["area_key"])
        if area is None:
            staff = _match_staffing(row["area_key"], row["area_name"], by_key, by_name)
            cover = (coverage or {}).get(row["area_key"])
            notice = (notices or {}).get(row["area_key"])
            area = AreaInput(
                area_key=row["area_key"],
                area_name=row["area_name"],
                org_level=row["org_level"] or "",
                years={},
                staffing_actual=staff["actual_count"] if staff else None,
                staffing_standard=staff["standard_count"] if staff else None,
                staffing_reference_at=staff["reference_at"] if staff else None,
                staffing_source_url=staff["source_url"] if staff else None,
                staffing_verified=staff["verify_status"] if staff else None,
                detail_url=row["detail_url"],
                area_website=row["area_website"],
                source_url=row["source_url"],
                reference_at=row["reference_at"],
                notice_url=notice["url"] if notice else None,
                notice_confirmed=bool(notice and notice["status"] == "confirmed"),
                coverage_status=cover["status"] if cover else None,
            )
            grouped[row["area_key"]] = area
        elif row["area_website"] and not area.area_website:
            area.area_website = row["area_website"]
        area.years[row["exam_year"]] = YearStat(
            exam_year=row["exam_year"],
            opened=None if row["opened"] is None else bool(row["opened"]),
            openings=row["openings"],
            applicants=row["applicants"],
            applicants_known=bool(row["applicants_known"]),
            listed_count=row["listed_count"],
            appointed_count=row["appointed_count"],
            remaining_count=row["remaining_count"],
        )
    return list(grouped.values())


def _save_recruitment(db_path, source: str, run_id: int, records: list[dict], replace_missing: bool = True) -> int:
    written = 0
    with connect(db_path) as conn:
        existing = {
            (row["exam_type"], row["subject"], row["exam_year"], row["area_key"]): row
            for row in conn.execute("SELECT * FROM recruitment_records WHERE source_code = ?", (source,))
        }
        seen = set()
        for record in records:
            key = (record["exam_type"], record["subject"], record["exam_year"], record["area_key"])
            seen.add(key)
            old = existing.get(key)
            if old:
                _log_changes(conn, source, run_id, old, record)
            conn.execute(
                """
                INSERT INTO recruitment_records (
                    source_code, fetch_run_id, exam_year, exam_type, subject, affiliation, org_level,
                    area_name, area_key, openings, applicants, applicants_known, listed_count,
                    appointed_count, remaining_count, opened, reference_at, source_url, detail_url,
                    area_website, observed_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_code, exam_type, subject, exam_year, area_key) DO UPDATE SET
                    fetch_run_id=excluded.fetch_run_id, affiliation=excluded.affiliation, org_level=excluded.org_level,
                    area_name=excluded.area_name, openings=excluded.openings, applicants=excluded.applicants,
                    applicants_known=excluded.applicants_known, listed_count=excluded.listed_count,
                    appointed_count=excluded.appointed_count, remaining_count=excluded.remaining_count,
                    opened=excluded.opened, reference_at=excluded.reference_at, source_url=excluded.source_url,
                    detail_url=excluded.detail_url,
                    area_website=COALESCE(excluded.area_website, recruitment_records.area_website),
                    observed_at=excluded.observed_at, raw_json=excluded.raw_json
                """,
                (
                    source,
                    run_id,
                    record["exam_year"],
                    record["exam_type"],
                    record["subject"],
                    record["affiliation"],
                    record["org_level"],
                    record["area_name"],
                    record["area_key"],
                    record["openings"],
                    record["applicants"],
                    1 if record["applicants_known"] else 0,
                    record["listed_count"],
                    record["appointed_count"],
                    record["remaining_count"],
                    None if record["opened"] is None else int(bool(record["opened"])),
                    record.get("reference_at"),
                    record.get("source_url"),
                    record.get("detail_url"),
                    record.get("area_website"),
                    now_iso(),
                    json.dumps(record, ensure_ascii=False, default=str),
                ),
            )
            recruitment_id = conn.execute(
                """
                SELECT id FROM recruitment_records
                WHERE source_code = ? AND exam_type = ? AND subject = ? AND exam_year = ? AND area_key = ?
                """,
                (source, record["exam_type"], record["subject"], record["exam_year"], record["area_key"]),
            ).fetchone()["id"]
            _replace_children(conn, recruitment_id, record)
            written += 1
        if replace_missing:
            for key, old in existing.items():
                if key in seen:
                    continue
                _log_removal(conn, source, run_id, old)
                conn.execute("DELETE FROM recruitment_records WHERE id = ?", (old["id"],))
        return written


def _replace_children(conn, recruitment_id: int, record: dict) -> None:
    conn.execute("DELETE FROM exam_openings WHERE recruitment_id = ?", (recruitment_id,))
    conn.execute("DELETE FROM candidate_counts WHERE recruitment_id = ?", (recruitment_id,))
    conn.execute("DELETE FROM account_call_history WHERE recruitment_id = ?", (recruitment_id,))
    conn.execute(
        "INSERT INTO exam_openings(recruitment_id, openings, opened) VALUES (?, ?, ?)",
        (recruitment_id, record["openings"], None if record["opened"] is None else int(bool(record["opened"]))),
    )
    conn.execute(
        "INSERT INTO candidate_counts(recruitment_id, applicants, known) VALUES (?, ?, ?)",
        (recruitment_id, record["applicants"], 1 if record["applicants_known"] else 0),
    )
    conn.execute(
        """
        INSERT INTO account_call_history(
            recruitment_id, listed_count, appointed_count, remaining_count, call_rank_reached,
            announced_at, source_url, evidence_note, exam_year, area_name, subject, exam_type
        ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            recruitment_id,
            record["listed_count"],
            record["appointed_count"],
            record["remaining_count"],
            record.get("source_url"),
            "จำนวนเรียกบรรจุคือตัวเลขบรรจุจากหน้ารวบรวม ไม่ใช่จำนวนตำแหน่งเปิดรับ หน้านี้ไม่มีลำดับบัญชีที่เรียกถึงและไม่มีวันที่ประกาศเรียก",
            record["exam_year"],
            record["area_name"],
            record["subject"],
            record["exam_type"],
        ),
    )


def _log_changes(conn, source: str, run_id: int, old, record: dict) -> None:
    for field in COMPARE_FIELDS:
        old_value = old[field]
        new_value = record[field]
        if isinstance(new_value, bool):
            new_value = int(new_value)
        if old_value != new_value and not (old_value is None and new_value is None):
            conn.execute(
                """
                INSERT INTO record_changes(source_code, area_key, area_name, exam_year, field_name, old_value, new_value, changed_at, fetch_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source, record["area_key"], record["area_name"], record["exam_year"], field, _text(old_value), _text(new_value), now_iso(), run_id),
            )


def _log_removal(conn, source: str, run_id: int, old) -> None:
    conn.execute(
        """
        INSERT INTO record_changes(source_code, area_key, area_name, exam_year, field_name, old_value, new_value, changed_at, fetch_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source, old["area_key"], old["area_name"], old["exam_year"], "record", "มีข้อมูล", "หายจากหน้าต้นทาง", now_iso(), run_id),
    )


def _save_summaries(db_path, source: str, run_id: int, summaries: list[dict]) -> None:
    with connect(db_path) as conn:
        for item in summaries:
            conn.execute(
                """
                INSERT INTO subject_summaries (
                    source_code, fetch_run_id, exam_type, subject, exam_year, areas_opened, openings,
                    applicants, applicants_known, listed_count, appointed_count, remaining_count, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_code, exam_type, subject, exam_year) DO UPDATE SET
                    fetch_run_id=excluded.fetch_run_id, areas_opened=excluded.areas_opened, openings=excluded.openings,
                    applicants=excluded.applicants, applicants_known=excluded.applicants_known,
                    listed_count=excluded.listed_count, appointed_count=excluded.appointed_count,
                    remaining_count=excluded.remaining_count, note=excluded.note
                """,
                (
                    source,
                    run_id,
                    item["exam_type"],
                    item["subject"],
                    item["exam_year"],
                    item.get("areas_opened"),
                    item.get("openings"),
                    item.get("applicants"),
                    1 if item.get("applicants_known") else 0,
                    item.get("listed_count"),
                    item.get("appointed_count"),
                    item.get("remaining_count"),
                    item.get("note"),
                ),
            )


def _save_staffing(db_path, source: str, run_id: int, item: dict) -> None:
    with connect(db_path) as conn:
        existing = conn.execute(
            """
            SELECT * FROM staffing_records
            WHERE source_code = ? AND scope = ? AND subject = ? AND area_name = ?
            """,
            (source, item["scope"], item["subject"], item.get("area_name") or ""),
        ).fetchone()
        actual = item.get("actual_count")
        standard = item.get("standard_count")
        reference_at = item.get("reference_at")
        if existing is not None:
            if actual is None:
                actual = existing["actual_count"]
            if standard is None:
                standard = existing["standard_count"]
            if reference_at is None:
                reference_at = existing["reference_at"]
        gap = actual - standard if actual is not None and standard is not None else item.get("gap_count")
        if existing is not None:
            for field_name, new_value in (
                ("actual_count", actual),
                ("standard_count", standard),
                ("gap_count", gap),
                ("reference_at", reference_at),
            ):
                old_value = existing[field_name]
                if old_value != new_value and not (old_value is None and new_value is None):
                    conn.execute(
                        """
                        INSERT INTO record_changes(
                            source_code, area_key, area_name, exam_year, field_name, old_value, new_value, changed_at, fetch_run_id
                        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
                        """,
                        (
                            source,
                            item.get("area_key") or existing["area_key"],
                            item.get("area_name") or "",
                            "staffing_" + field_name,
                            _text(old_value),
                            _text(new_value),
                            now_iso(),
                            run_id,
                        ),
                    )
        conn.execute(
            """
            INSERT INTO staffing_records (
                source_code, fetch_run_id, scope, area_name, subject, standard_count, actual_count,
                gap_count, retire_10y, need_10y, reference_at, reference_note, source_url, observed_at, raw_json,
                area_key, verify_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_code, scope, subject, area_name) DO UPDATE SET
                fetch_run_id=excluded.fetch_run_id, standard_count=excluded.standard_count, actual_count=excluded.actual_count,
                gap_count=excluded.gap_count, retire_10y=excluded.retire_10y, need_10y=excluded.need_10y,
                reference_at=excluded.reference_at, reference_note=excluded.reference_note,
                source_url=excluded.source_url, observed_at=excluded.observed_at, raw_json=excluded.raw_json,
                area_key=COALESCE(excluded.area_key, staffing_records.area_key),
                verify_status=COALESCE(excluded.verify_status, staffing_records.verify_status)
            """,
            (
                source,
                run_id,
                item["scope"],
                item.get("area_name") or "",
                item["subject"],
                standard,
                actual,
                gap,
                item.get("retire_10y"),
                item.get("need_10y"),
                reference_at,
                item.get("reference_note"),
                item.get("source_url"),
                now_iso(),
                json.dumps(item, ensure_ascii=False, default=str),
                item.get("area_key"),
                item.get("verify_status"),
            ),
        )


def _set_website(db_path, area_name: str, website: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE recruitment_records SET area_website = ? WHERE source_code = 'kruphuchuay' AND area_name = ?",
            (website, area_name),
        )


def _replace_warnings(db_path, source: str, run_id: int, warnings: list[str]) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM data_warnings WHERE source_code = ?", (source,))
        for message in warnings:
            conn.execute(
                "INSERT INTO data_warnings(source_code, area_key, message, created_at, fetch_run_id) VALUES (?, ?, ?, ?, ?)",
                (source, None, message, now_iso(), run_id),
            )


def _start_run(db_path, source: str, started: str) -> int:
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO fetch_runs(source_code, started_at, status, records_written, structure_ok) VALUES (?, ?, ?, 0, 0)",
            (source, started, "running"),
        )
        return int(cursor.lastrowid)


def _complete_run(db_path, run_id: int, status: str, http_status, message: str, records: int, digest, structure_ok: bool) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE fetch_runs
            SET finished_at = ?, status = ?, http_status = ?, message = ?, records_written = ?, content_hash = ?, structure_ok = ?
            WHERE id = ?
            """,
            (now_iso(), status, http_status, message, records, digest, 1 if structure_ok else 0, run_id),
        )


def _finish_run(db_path, source: str, status: str, http_status, message: str, records: int, digest, structure_ok: bool, started: str | None = None) -> None:
    run_id = _start_run(db_path, source, started or now_iso())
    _complete_run(db_path, run_id, status, http_status, message, records, digest, structure_ok)


def _acquire_lock(db_path) -> bool:
    with connect(db_path) as conn:
        current = get_pref(conn, "refresh_lock")
        if current:
            try:
                stamp = datetime.fromisoformat(current)
                if datetime.now().astimezone() - stamp < timedelta(minutes=10):
                    return False
            except ValueError:
                pass
        set_pref(conn, "refresh_lock", now_iso())
        return True


def _release_lock(db_path) -> None:
    with connect(db_path) as conn:
        set_pref(conn, "refresh_lock", "")


def _text(value) -> str:
    return "" if value is None else str(value)
