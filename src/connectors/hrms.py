from __future__ import annotations

import re

from src.labels import SUBJECT

PAGE_URL = "https://hrms.obec.go.th/info/majorStandard/major10Years/"
ORIGIN = "https://hrms.obec.go.th"


def discover_report_script(page_html: str, runtime_js: str) -> str | None:
    runtime_src = re.search(r"/_nuxt/runtime\.[a-f0-9]+\.js", page_html or "")
    if runtime_src is None and "major10-years-report" not in (runtime_js or ""):
        return None
    names = dict(re.findall(r'(\d+):"(components/[^"]+)"', runtime_js or ""))
    hashes = dict(re.findall(r'(\d+):"([a-f0-9]{16,})"', runtime_js or ""))
    for chunk_id, name in names.items():
        if name.endswith("major10-years-report") and chunk_id in hashes:
            return f"{ORIGIN}/_nuxt/{name}.{hashes[chunk_id]}.js"
    return None


def parse_english_staffing(script: str) -> dict | None:
    match = re.search(r'\{id:\d+,majorTitle:"ภาษาอังกฤษ",([^{}]+)\}', script or "")
    if not match:
        return None
    fields = {key: int(value) for key, value in re.findall(r"([A-Za-z0-9_]+):(-?\d+)", match.group(1))}
    standard = fields.get("stadardMajors", fields.get("standardMajors"))
    actual = fields.get("teacherNumber")
    if standard is None or actual is None:
        return None
    return {
        "subject": SUBJECT,
        "scope": "national",
        "area_name": "",
        "standard_count": standard,
        "actual_count": actual,
        "gap_count": fields.get("differentNumber", actual - standard),
        "retire_10y": fields.get("sumExit"),
        "need_10y": fields.get("sumNeed"),
        "yearly": {key: value for key, value in fields.items() if key.startswith(("retire", "need"))},
        "reference_note": "ตารางความต้องการทดแทนอัตราครู 10 ปี ระดับประเทศในหน้า HRMS ชุดข้อมูลที่ฝังในหน้าไม่มีวันที่ปรับปรุงแยกต่างหาก",
    }
