from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from src.db import connect, now_iso

PDF_DIR = Path(__file__).resolve().parent.parent / "data" / "source_pdfs"

PDF_SCHEMA = """
CREATE TABLE IF NOT EXISTS pdf_documents (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    document_type TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    extracted_chars INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    message TEXT,
    imported_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pdf_pages (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES pdf_documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    text_chars INTEGER NOT NULL DEFAULT 0,
    UNIQUE(document_id, page_number)
);
CREATE TABLE IF NOT EXISTS pdf_extractions (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES pdf_documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    area_name TEXT,
    subject TEXT,
    exam_year INTEGER,
    value_text TEXT,
    value_int INTEGER,
    verification_status TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pdf_review_items (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES pdf_documents(id) ON DELETE CASCADE,
    page_number INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'รอตรวจสอบ',
    created_at TEXT NOT NULL
);
"""


@dataclass
class PdfImportResult:
    filename: str
    document_type: str
    status: str
    pages: int
    extracted_chars: int
    review_items: int
    message: str


def ensure_pdf_schema(db_path) -> None:
    with connect(db_path) as conn:
        conn.executescript(PDF_SCHEMA)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_document(filename: str) -> str:
    name = filename.lower()
    if "แนวทางวิเคราะห์" in name:
        return "คู่มือแนวทางเลือกเขต"
    if "สรุปอัตราเปิดสอบ" in name or "เกษียณ" in name:
        return "อัตราเปิดสอบและเกษียณ 2569-2571"
    for year, token in ((2566, "66"), (2567, "67"), (2568, "68")):
        if f"ปี-{token}" in name or f"ปี {token}" in name:
            return f"บัญชีคงเหลือ {year}"
    return "เอกสาร PDF อื่น"


def _year_from_type(document_type: str) -> int | None:
    found = re.search(r"(256[6-8])", document_type)
    return int(found.group(1)) if found else None


def _page_text(page) -> tuple[str, str]:
    try:
        text = page.extract_text() or ""
    except Exception as exc:  # pragma: no cover - depends on malformed PDFs
        return "", f"อ่านไม่สำเร็จ: {exc}"
    text = text.replace("\x00", "").strip()
    return text, "ข้อความจาก PDF"


def _add_review(conn, document_id: int, page_number: int | None, kind: str, message: str, evidence: str = "") -> None:
    conn.execute(
        """
        INSERT INTO pdf_review_items(document_id, page_number, kind, message, evidence, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'รอตรวจสอบ', ?)
        """,
        (document_id, page_number, kind, message, evidence[:4000], now_iso()),
    )


def _add_extraction(conn, document_id: int, page_number: int, field_name: str, *,
                    area_name: str | None = None, subject: str | None = None,
                    exam_year: int | None = None, value_text: str | None = None,
                    value_int: int | None = None, status: str = "หลักฐานดิบ") -> None:
    conn.execute(
        """
        INSERT INTO pdf_extractions(
            document_id, page_number, field_name, area_name, subject, exam_year,
            value_text, value_int, verification_status, evidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, page_number, field_name, area_name, subject, exam_year,
         value_text, value_int, status, value_text or ""),
    )


def _extract_document_evidence(conn, document_id: int, document_type: str, pages: list[str]) -> int:
    review_count = 0
    if document_type.startswith("บัญชีคงเหลือ"):
        year = _year_from_type(document_type)
        english_pages = []
        for number, text in enumerate(pages, start=1):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            matches = [line for line in lines if "ภาษาอังกฤษ" in line or "อังกฤษ" in line]
            if matches:
                english_pages.append(number)
                for line in matches[:40]:
                    _add_extraction(
                        conn,
                        document_id,
                        number,
                        "แถวหลักฐานเอกภาษาอังกฤษ",
                        subject="ภาษาอังกฤษ",
                        exam_year=year,
                        value_text=line,
                        status="ต้องตรวจสอบตัวเลขและชื่อเขต",
                    )
        if not english_pages:
            _add_review(conn, document_id, None, "ไม่พบคำสำคัญ", "ไม่พบแถวหรือคอลัมน์ที่ระบุภาษาอังกฤษจากข้อความที่สกัดได้")
            review_count += 1
        else:
            _add_review(
                conn,
                document_id,
                None,
                "ต้องตรวจสอบตาราง",
                "พบหลักฐานภาษาอังกฤษแล้ว แต่ระบบไม่เดาตัวเลขหรือชื่อเขตจากข้อความที่จัดวางซับซ้อน ต้องตรวจแถวก่อนนำเข้าข้อมูลหลัก",
                f"พบในหน้า: {', '.join(map(str, english_pages))}",
            )
            review_count += 1
    elif document_type == "อัตราเปิดสอบและเกษียณ 2569-2571":
        _add_review(
            conn,
            document_id,
            None,
            "ข้อมูลรวมทุกวิชา",
            "เอกสารนี้มีอัตราเกษียณรวมทุกวิชา ไม่ให้นำไปแทนตำแหน่งเอกภาษาอังกฤษหรือเพิ่มคะแนนเอกอังกฤษโดยตรง",
        )
        review_count += 1
        for number, text in enumerate(pages, start=1):
            for line in text.splitlines():
                line = line.strip()
                if line and ("สพป." in line or "สพม." in line) and len(line) < 500:
                    _add_extraction(conn, document_id, number, "แถวอัตราเปิดสอบ/เกษียณดิบ", value_text=line, status="ข้อมูลประกอบ ต้องตรวจสอบ")
    else:
        _add_review(
            conn,
            document_id,
            None,
            "คู่มือ/หลักเกณฑ์",
            "เอกสารนี้ถูกเก็บเป็นหลักเกณฑ์และหลักฐานประกอบ ไม่ถูกแปลงเป็นตัวเลขคะแนนอัตโนมัติ",
        )
        review_count += 1
    return review_count


def import_pdf(path: Path, db_path) -> PdfImportResult:
    ensure_pdf_schema(db_path)
    digest = sha256_file(path)
    document_type = classify_document(path.name)
    with connect(db_path) as conn:
        existing = conn.execute("SELECT id, filename FROM pdf_documents WHERE sha256 = ?", (digest,)).fetchone()
        if existing:
            conn.execute("UPDATE pdf_documents SET last_seen_at = ?, path = ? WHERE id = ?", (now_iso(), str(path), existing["id"]))
            return PdfImportResult(path.name, document_type, "ข้ามไฟล์เดิม", 0, 0, 0, "ไฟล์ hash เดิมถูกนำเข้าแล้ว")
        try:
            reader = PdfReader(str(path))
            page_texts = []
            methods = []
            for page in reader.pages:
                text, method = _page_text(page)
                page_texts.append(text)
                methods.append(method)
        except Exception as exc:
            conn.execute(
                """
                INSERT INTO pdf_documents(filename, path, sha256, document_type, status, message, imported_at, last_seen_at)
                VALUES (?, ?, ?, ?, 'อ่านไม่สำเร็จ', ?, ?, ?)
                """,
                (path.name, str(path), digest, document_type, str(exc), now_iso(), now_iso()),
            )
            return PdfImportResult(path.name, document_type, "อ่านไม่สำเร็จ", 0, 0, 1, str(exc))
        conn.execute(
            """
            INSERT INTO pdf_documents(filename, path, sha256, document_type, page_count, extracted_chars, status, message, imported_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, 'นำเข้าแล้ว', ?, ?, ?)
            """,
            (path.name, str(path), digest, document_type, len(page_texts), sum(map(len, page_texts)), "เก็บข้อความรายหน้าและหลักฐานดิบแล้ว", now_iso(), now_iso()),
        )
        document_id = conn.execute("SELECT id FROM pdf_documents WHERE sha256 = ?", (digest,)).fetchone()["id"]
        for number, (text, method) in enumerate(zip(page_texts, methods), start=1):
            conn.execute(
                "INSERT INTO pdf_pages(document_id, page_number, text, extraction_method, text_chars) VALUES (?, ?, ?, ?, ?)",
                (document_id, number, text, method, len(text)),
            )
            if len(text) < 30:
                _add_review(conn, document_id, number, "ข้อความน้อย", "หน้านี้มีข้อความสกัดได้น้อย อาจต้องตรวจด้วยภาพหรือ OCR", text)
        review_count = _extract_document_evidence(conn, document_id, document_type, page_texts)
        return PdfImportResult(path.name, document_type, "นำเข้าแล้ว", len(page_texts), sum(map(len, page_texts)), review_count, "นำเข้าไฟล์และเก็บหลักฐานรายหน้าแล้ว")


def sync_local_pdfs(db_path, pdf_dir: Path = PDF_DIR) -> list[PdfImportResult]:
    ensure_pdf_schema(db_path)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(pdf_dir.glob("*.pdf")):
        results.append(import_pdf(path, db_path))
    return results


def list_pdf_documents(db_path) -> list[dict]:
    ensure_pdf_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.*, COUNT(DISTINCT p.id) AS stored_pages, COUNT(DISTINCT r.id) AS review_count
            FROM pdf_documents d
            LEFT JOIN pdf_pages p ON p.document_id = d.id
            LEFT JOIN pdf_review_items r ON r.document_id = d.id AND r.status = 'รอตรวจสอบ'
            GROUP BY d.id
            ORDER BY d.imported_at DESC, d.filename
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_pdf_review_items(db_path, limit: int = 200) -> list[dict]:
    ensure_pdf_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.*, d.filename
            FROM pdf_review_items r
            JOIN pdf_documents d ON d.id = r.document_id
            WHERE r.status = 'รอตรวจสอบ'
            ORDER BY r.created_at DESC, d.filename
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def pdf_overview(db_path) -> dict:
    ensure_pdf_schema(db_path)
    with connect(db_path) as conn:
        documents = conn.execute("SELECT COUNT(*) AS n FROM pdf_documents").fetchone()["n"]
        pages = conn.execute("SELECT COUNT(*) AS n FROM pdf_pages").fetchone()["n"]
        reviews = conn.execute("SELECT COUNT(*) AS n FROM pdf_review_items WHERE status = 'รอตรวจสอบ'").fetchone()["n"]
        extractions = conn.execute("SELECT COUNT(*) AS n FROM pdf_extractions").fetchone()["n"]
    return {"documents": documents, "pages": pages, "reviews": reviews, "extractions": extractions}
