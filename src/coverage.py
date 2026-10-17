from __future__ import annotations

from src.labels import (
    COVERAGE_ANNOUNCED,
    COVERAGE_NONE,
    COVERAGE_SOURCE_CLOSED,
    COVERAGE_WAITING,
    EXAM_GENERAL,
    SUBJECT,
)


def build_coverage(
    english_rows: list[dict],
    waiting_names: list[str] | None,
    closed_names: list[str] | None,
    index_names: list[str] | None,
    *,
    reference_at: str | None,
    subject_url: str,
    waiting_url: str,
    closed_url: str,
    index_url: str,
) -> tuple[list[dict], list[str]]:
    """Classify districts only from lists that were actually parsed.

    A missing list stays unknown. It is not treated as proof that a district is closed.
    """
    warnings: list[str] = []
    by_name: dict[str, dict] = {}
    english_names = set()
    for row in english_rows:
        if row.get("exam_year") != 2569 or not row.get("opened") or not row.get("openings"):
            continue
        name = row["area_name"]
        english_names.add(name)
        openings = row["openings"]
        by_name[name] = {
            "area_key": row["area_key"],
            "area_name": name,
            "exam_year": 2569,
            "exam_type": EXAM_GENERAL,
            "subject": SUBJECT,
            "status": COVERAGE_ANNOUNCED,
            "evidence": (
                f"หน้ารายวิชาเอกภาษาอังกฤษระบุเปิดรับ {openings} ตำแหน่ง "
                "ยังไม่มีจำนวนผู้สมัครบนหน้านี้ และวันที่บนหน้าคือวันที่ข้อมูล ไม่ใช่วันที่ประกาศของเขต"
            ),
            "source_url": row.get("source_url") or subject_url,
            "announced_at": None,
            "application_start": None,
            "application_end": None,
            "reference_at": reference_at,
        }

    if closed_names is None:
        warnings.append("ตรวจสอบรายชื่อเขตที่หน้าต้นทางระบุว่าไม่เปิดสอบไม่ได้")
    else:
        for name in closed_names:
            if name in english_names:
                warnings.append(f"{name}: อยู่ทั้งหน้ารายวิชาและหน้ารายชื่อไม่เปิดสอบ จึงยึดหน้ารายวิชา")
                continue
            by_name[name] = _named(
                name,
                COVERAGE_SOURCE_CLOSED,
                "หน้ารายชื่อเขตที่ไม่เปิดสอบมีชื่อเขตนี้ เป็นถ้อยคำของหน้าต้นทาง ไม่ได้อนุมานจากช่องว่าง และไม่ได้บอกว่าเอกภาษาอังกฤษถูกตัดแยกต่างหาก",
                closed_url,
            )

    if waiting_names is None:
        warnings.append("ตรวจสอบรายชื่อเขตรอประกาศไม่ได้")
    else:
        for name in waiting_names:
            if name in by_name:
                warnings.append(f"{name}: อยู่ทั้งรายชื่อรอประกาศและรายการที่มีสถานะอื่น จึงไม่เปลี่ยนสถานะเดิม")
                continue
            by_name[name] = _named(
                name,
                COVERAGE_WAITING,
                "หน้ารายชื่อเขตรอประกาศมีชื่อเขตนี้ ยังไม่ทราบว่าจะเปิดเอกภาษาอังกฤษหรือไม่ จึงสรุปไม่ได้ว่าไม่เปิดสอบ",
                waiting_url,
            )

    if index_names is None or waiting_names is None or closed_names is None:
        warnings.append("ยังไม่แยกเขตที่ไม่มีข้อมูลเอกภาษาอังกฤษ เพราะรายชื่อสถานะยังไม่ครบ")
    else:
        known = set(by_name)
        for name in index_names:
            if name in known:
                continue
            by_name[name] = _named(
                name,
                COVERAGE_NONE,
                "เขตนี้มีชื่อในหน้ารายชื่อเขตที่ประกาศตำแหน่งแล้ว แต่ไม่มีแถวเอกภาษาอังกฤษในหน้ารายวิชา จึงยังไม่มีข้อมูลตำแหน่งเอกนี้ และไม่ได้แปลว่าไม่เปิดสอบ",
                index_url,
            )
    return list(by_name.values()), warnings


def _named(name: str, status: str, evidence: str, url: str) -> dict:
    return {
        "area_key": f"name:{name}",
        "area_name": name,
        "exam_year": 2569,
        "exam_type": EXAM_GENERAL,
        "subject": SUBJECT,
        "status": status,
        "evidence": evidence,
        "source_url": url,
        "announced_at": None,
        "application_start": None,
        "application_end": None,
        "reference_at": None,
    }
