from __future__ import annotations

from src.labels import MISSING

VERIFIED_KEY = "จับคู่ด้วยรหัสเขตจาก URL หน้ารายเขตและชื่อตรงกัน"
UNLINKED = "ยังจับคู่รหัสเขตไม่ได้"


def staffing_metrics(actual: int | None, standard: int | None) -> dict:
    """Gap is actual minus standard. Percent is withheld when the standard is missing or zero."""
    if actual is None or standard is None:
        return {
            "gap": None,
            "shortage": None,
            "fill_percent": None,
            "level": MISSING,
            "reason": "ไม่มีจำนวนครูที่มีจริงหรือจำนวนตามเกณฑ์",
        }
    gap = actual - standard
    if standard <= 0:
        return {
            "gap": gap,
            "shortage": None,
            "fill_percent": None,
            "level": "เกณฑ์เป็นศูนย์",
            "reason": "เกณฑ์เป็นศูนย์ จึงไม่คำนวณเปอร์เซ็นต์และไม่ตีความว่าขาดหรือเกิน",
        }
    fill = actual / standard * 100
    shortage = max(0, standard - actual)
    if gap >= 0:
        level = "ไม่ต่ำกว่าเกณฑ์"
    elif fill >= 90:
        level = "ขาดเล็กน้อย"
    elif fill >= 80:
        level = "ขาดปานกลาง"
    else:
        level = "ขาดมาก"
    return {
        "gap": gap,
        "shortage": shortage,
        "fill_percent": fill,
        "level": level,
        "reason": (
            f"มีครู {actual} คน จากเกณฑ์ {standard} คน ผลต่าง {gap:+d} "
            f"คิดเป็น {fill:.1f}% ของเกณฑ์ ระดับ{level} "
            "ตัวเลขนี้ไม่ใช่จำนวนตำแหน่งสอบ และไม่ได้แปลว่าจะเปิดสอบหรือเรียกบรรจุเพิ่ม"
        ),
    }


def shortage_score(actual: int | None, standard: int | None) -> tuple[float | None, str]:
    metrics = staffing_metrics(actual, standard)
    if metrics["fill_percent"] is None:
        return None, metrics["reason"]
    short_share = max(0.0, (standard - actual) / standard)
    score = max(0.0, min(100.0, short_share / 0.20 * 100))
    return score, metrics["reason"] + " น้ำหนักส่วนนี้ถูกจำกัด ไม่ให้การขาดครูตัดสินอันดับแทนข้อมูลสอบ"


def link_staffing_records(conn) -> int:
    """Attach a staffing row only when the detail URL and the exact district name agree."""
    linked = 0
    rows = conn.execute(
        "SELECT id, area_name, source_url, area_key FROM staffing_records WHERE scope = 'area'"
    ).fetchall()
    for row in rows:
        if row["area_key"]:
            continue
        match = conn.execute(
            """
            SELECT area_key FROM recruitment_records
            WHERE source_code = 'kruphuchuay' AND detail_url = ? AND area_name = ?
            LIMIT 1
            """,
            (row["source_url"], row["area_name"]),
        ).fetchone()
        if match is None:
            conn.execute(
                "UPDATE staffing_records SET verify_status = ? WHERE id = ? AND verify_status IS NULL",
                (UNLINKED, row["id"]),
            )
            continue
        conn.execute(
            "UPDATE staffing_records SET area_key = ?, verify_status = ? WHERE id = ?",
            (match["area_key"], VERIFIED_KEY, row["id"]),
        )
        linked += 1
    return linked
