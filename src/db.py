from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "radar.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    http_status INTEGER,
    message TEXT,
    records_written INTEGER DEFAULT 0,
    content_hash TEXT,
    structure_ok INTEGER
);

CREATE TABLE IF NOT EXISTS recruitment_records (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    fetch_run_id INTEGER,
    exam_year INTEGER NOT NULL,
    exam_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    affiliation TEXT NOT NULL,
    org_level TEXT,
    area_name TEXT NOT NULL,
    area_key TEXT NOT NULL,
    openings INTEGER,
    applicants INTEGER,
    applicants_known INTEGER NOT NULL DEFAULT 0,
    listed_count INTEGER,
    appointed_count INTEGER,
    remaining_count INTEGER,
    opened INTEGER,
    reference_at TEXT,
    source_url TEXT,
    detail_url TEXT,
    area_website TEXT,
    observed_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(source_code, exam_type, subject, exam_year, area_key)
);

CREATE TABLE IF NOT EXISTS exam_openings (
    id INTEGER PRIMARY KEY,
    recruitment_id INTEGER NOT NULL REFERENCES recruitment_records(id) ON DELETE CASCADE,
    openings INTEGER,
    opened INTEGER
);

CREATE TABLE IF NOT EXISTS candidate_counts (
    id INTEGER PRIMARY KEY,
    recruitment_id INTEGER NOT NULL REFERENCES recruitment_records(id) ON DELETE CASCADE,
    applicants INTEGER,
    known INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS account_call_history (
    id INTEGER PRIMARY KEY,
    recruitment_id INTEGER NOT NULL REFERENCES recruitment_records(id) ON DELETE CASCADE,
    listed_count INTEGER,
    appointed_count INTEGER,
    remaining_count INTEGER
);

CREATE TABLE IF NOT EXISTS staffing_records (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    fetch_run_id INTEGER,
    scope TEXT NOT NULL,
    area_name TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL,
    standard_count INTEGER,
    actual_count INTEGER,
    gap_count INTEGER,
    retire_10y INTEGER,
    need_10y INTEGER,
    reference_at TEXT,
    reference_note TEXT,
    source_url TEXT,
    observed_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(source_code, scope, subject, area_name)
);

CREATE TABLE IF NOT EXISTS subject_summaries (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    fetch_run_id INTEGER,
    exam_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    exam_year INTEGER NOT NULL,
    areas_opened INTEGER,
    openings INTEGER,
    applicants INTEGER,
    applicants_known INTEGER NOT NULL DEFAULT 0,
    listed_count INTEGER,
    appointed_count INTEGER,
    remaining_count INTEGER,
    note TEXT,
    UNIQUE(source_code, exam_type, subject, exam_year)
);

CREATE TABLE IF NOT EXISTS saved_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_changes (
    id INTEGER PRIMARY KEY,
    source_code TEXT,
    area_key TEXT,
    area_name TEXT,
    exam_year INTEGER,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL,
    fetch_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS data_warnings (
    id INTEGER PRIMARY KEY,
    source_code TEXT,
    area_key TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    fetch_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS coverage_records (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    exam_year INTEGER NOT NULL,
    exam_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    area_key TEXT NOT NULL,
    area_name TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence TEXT NOT NULL,
    source_url TEXT,
    announced_at TEXT,
    application_start TEXT,
    application_end TEXT,
    observed_at TEXT NOT NULL,
    UNIQUE(source_code, exam_type, subject, exam_year, area_key)
);

CREATE TABLE IF NOT EXISTS source_documents (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    area_key TEXT,
    area_name TEXT,
    exam_year INTEGER,
    exam_type TEXT,
    subject TEXT,
    doc_kind TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    announced_at TEXT,
    observed_at TEXT NOT NULL,
    UNIQUE(url)
);

CREATE TABLE IF NOT EXISTS call_events (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    exam_year INTEGER NOT NULL,
    exam_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    area_key TEXT NOT NULL,
    area_name TEXT NOT NULL,
    call_rank_reached INTEGER,
    appointed_count INTEGER,
    remaining_count INTEGER,
    announced_at TEXT,
    source_url TEXT NOT NULL,
    evidence TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(source_code, exam_type, subject, exam_year, area_key, source_url)
);
"""

SOURCE_SEED = [
    (
        "kruphuchuay",
        "ครูผู้ช่วยดอทคอม — สถิติวิชาเอกภาษาอังกฤษ",
        "https://www.kruphuchuay.com/report.php?r=4/5",
        "หน้ารวบรวมสถิติการคัดเลือกทั่วไป ใช้ประกอบการตัดสินใจ ต้องตรวจกับประกาศเขตอีกครั้ง",
    ),
    (
        "hrms_obec",
        "HRMS สพฐ. — ความต้องการทดแทนครู 10 ปี",
        "https://hrms.obec.go.th/info/majorStandard/major10Years/",
        "ข้อมูลระดับประเทศทั้งสังกัด ไม่ใช่จำนวนตำแหน่งสอบรายเขต",
    ),
    (
        "obec",
        "สำนักงานคณะกรรมการการศึกษาขั้นพื้นฐาน",
        "https://www.obec.go.th/",
        "หน้าแรกทางการ ไม่มีตารางสอบเอกภาษาอังกฤษที่ดึงอัตโนมัติได้",
    ),
    (
        "personnel_obec",
        "กลุ่มบริหารงานบุคคล สพฐ.",
        "https://personnel.obec.go.th/",
        "หน้าแรกทางการ ไม่มีตารางสอบเอกภาษาอังกฤษที่ดึงอัตโนมัติได้",
    ),
    (
        "otepc",
        "สำนักงานคณะกรรมการข้าราชการครูและบุคลากรทางการศึกษา",
        "https://www.otepc.go.th/",
        "หน้าแรกทางการ ไม่มีตารางสอบเอกภาษาอังกฤษที่ดึงอัตโนมัติได้",
    ),
    (
        "csv_import",
        "ไฟล์ที่ผู้ใช้นำเข้า",
        "local:csv",
        "ข้อมูลจาก CSV หรือ XLSX ที่ผู้ใช้ยืนยันเอง",
    ),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


STAFFING_COLUMNS = {
    "area_key": "TEXT",
    "verify_status": "TEXT",
}
CALL_HISTORY_COLUMNS = {
    "call_rank_reached": "INTEGER",
    "announced_at": "TEXT",
    "source_url": "TEXT",
    "evidence_note": "TEXT",
    "exam_year": "INTEGER",
    "area_name": "TEXT",
    "subject": "TEXT",
    "exam_type": "TEXT",
}


def init_db(db_path: Path | str = DEFAULT_DB) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(account_call_history)")}
        for column, kind in CALL_HISTORY_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE account_call_history ADD COLUMN {column} {kind}")
        staffing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(staffing_records)")}
        for column, kind in STAFFING_COLUMNS.items():
            if column not in staffing_columns:
                conn.execute(f"ALTER TABLE staffing_records ADD COLUMN {column} {kind}")
        conn.executemany(
            """
            INSERT INTO sources(code, name, url, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET name=excluded.name, url=excluded.url, notes=excluded.notes
            """,
            SOURCE_SEED,
        )


def get_pref(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM saved_preferences WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_pref(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO saved_preferences(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, now_iso()),
    )
