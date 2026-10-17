from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.connectors.gazette import document_matches
from src.connectors.importer import parse_import_file
from src.connectors.kruphuchuay import RELATED_URLS, REPORT_URL
from src.db import DEFAULT_DB, connect, init_db, set_pref
from src.labels import (
    COVERAGE_ANNOUNCED,
    COVERAGE_NONE,
    COVERAGE_OPEN,
    COVERAGE_SOURCE_CLOSED,
    COVERAGE_UNVERIFIED,
    COVERAGE_WAITING,
    CONFIDENCE_READY,
    MISSING,
    NOT_FOUND,
    NOT_OPEN,
    SIMULATION,
    STATUS_FAIL,
    SUBJECT,
)
from src.guide_rules import apply_choice_filters, evidence_review
from src.parsing import competition_text, format_thai_stamp
from src.pdf_ingest import PDF_DIR, list_pdf_documents, list_pdf_review_items, pdf_overview, sync_local_pdfs
from src.scenarios import applicant_increase, factor_outlook, future_calls, reach_rank, staffing_change, target_disadvantage
from src.scoring import evidence_notice, formula_text, normalize_weights, score_areas
from src.staffing import staffing_metrics
from src.strategy import choice_notes, classify_choices, decision_gaps
from src.service import import_records, load_dashboard, refresh_all, should_refresh

DB = DEFAULT_DB


def main() -> None:
    st.set_page_config(page_title="Teacher Exam Radar", layout="wide")
    st.markdown(
        """
        <style>
        html, body, [class*="css"] { font-family: "Leelawadee UI", "Sarabun", Tahoma, sans-serif; }
        .block-container { padding-top: 1rem; max-width: 1280px; }
        .radar-hero { background: linear-gradient(135deg, #123b5d 0%, #1d6b83 100%); color: white; border-radius: 18px; padding: 1.35rem 1.5rem; margin-bottom: 1rem; }
        .radar-hero h1 { margin: 0 0 .25rem 0; font-size: clamp(1.55rem, 3vw, 2.25rem); }
        .radar-hero p { margin: 0; opacity: .88; font-size: 1rem; }
        .radar-note { background: #f1f6f8; border-left: 4px solid #2b7a8b; padding: .7rem .9rem; border-radius: 8px; margin: .5rem 0 1rem; }
        .area-card-title { font-size: 1.12rem; font-weight: 700; color: #123b5d; margin-bottom: .25rem; }
        .area-card-score { font-size: 1.55rem; font-weight: 800; color: #16746b; }
        .area-card-label { font-size: .82rem; color: #64748b; }
        @media (max-width: 640px) { .block-container { padding: .65rem .75rem 1.5rem; } .radar-hero { padding: 1rem; border-radius: 14px; } }
        </style>
        """,
        unsafe_allow_html=True,
    )
    init_db(DB)
    _boot()
    snapshot = load_dashboard(DB)
    _header(snapshot)
    (
        focus_year,
        org_level,
        query,
        favorites_only,
        data_filter,
        coverage_status,
        weights,
        exclude_zone_one,
        exclude_low_history,
    ) = _filters(snapshot)
    view = load_dashboard(DB, focus_year, org_level, query, favorites_only, data_filter, coverage_status)
    view["areas"] = apply_choice_filters(
        view["areas"],
        exclude_zone_one=exclude_zone_one,
        exclude_low_history=exclude_low_history,
    )
    view["weights"] = weights
    scores = score_areas(view["areas"], view["focus_year"], weights) if view["focus_year"] else []
    _decision(view, scores)
    _metrics(view, scores)
    compare, rank, strategy, picked, history, scenario, monitor, sources, pdfs = st.tabs(
        ["เปรียบเทียบเขต", "อันดับและเหตุผล", "กลยุทธ์เลือกเขต", "เทียบเขตที่เลือก", "ประวัติเรียกบรรจุ", "จำลองสถานการณ์", "ติดตามแหล่งข้อมูล", "แหล่งข้อมูล", "PDF หลักฐาน"]
    )
    with compare:
        _compare_tab(view, scores)
    with rank:
        _rank_tab(scores)
    with strategy:
        _strategy_tab(view, scores)
    with picked:
        _picked_tab(view, scores)
    with history:
        _history_tab(view)
    with scenario:
        _scenario_tab(view)
    with monitor:
        _monitor_tab(view)
    with sources:
        _source_tab(view)
    with pdfs:
        _pdf_tab()


def _boot() -> None:
    if st.session_state.get("booted"):
        return
    st.session_state["booted"] = True
    with st.spinner("กำลังตรวจสอบและนำเข้า PDF ใน data/source_pdfs ..."):
        st.session_state["pdf_import_result"] = sync_local_pdfs(DB, PDF_DIR)
    if should_refresh(DB):
        with st.spinner("กำลังดึงข้อมูลจากแหล่งต้นทาง..."):
            st.session_state["refresh_result"] = refresh_all(DB)


def _header(snapshot: dict) -> None:
    st.markdown(
        '<div class="radar-hero"><h1>Teacher Exam Radar</h1><p>ช่วยเทียบเขตสอบครูผู้ช่วย สพฐ. เอกภาษาอังกฤษ จากหลักฐานที่ตรวจสอบได้</p></div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"**สถานะข้อมูล:** `{snapshot['status']}`")
        success = format_thai_stamp(snapshot["last_success"]) if snapshot["last_success"] else "ยังไม่เคยดึงสำเร็จ"
        st.caption(f"ดึงข้อมูลสำเร็จล่าสุด: {success}")
        source_time = format_thai_stamp(snapshot["reference_at"]) if snapshot["reference_at"] else MISSING
        st.caption(f"วันที่บนหน้าต้นทาง: {source_time}")
        if snapshot["status"] == STATUS_FAIL and snapshot["last_success"]:
            st.warning("การดึงครั้งล่าสุดไม่สำเร็จ ตารางด้านล่างเป็นข้อมูลครั้งล่าสุดที่ดึงสำเร็จ ไม่ใช่เวลาปัจจุบัน")
        if snapshot["last_message"]:
            st.caption(snapshot["last_message"][:500])
        if snapshot["failed_sources"]:
            st.warning("มีแหล่งที่ดึงไม่สำเร็จหรือยังไม่มีตารางสอบ รายการและเหตุผลอยู่ในแท็บแหล่งข้อมูล")
    with right:
        if st.button("รีเฟรชข้อมูล", type="primary", width="stretch"):
            with st.spinner("กำลังดึงข้อมูลใหม่..."):
                st.session_state["refresh_result"] = refresh_all(DB)
            st.rerun()
        st.link_button("เปิดหน้ารวบรวมต้นทาง", REPORT_URL, width="stretch")
        st.caption("หน้ารวบรวมไม่ใช่ประกาศทางการของเขต ใช้ปุ่มเว็บเขตในตารางเมื่อต้องการเปิดต้นสังกัด")


def _pdf_tab() -> None:
    st.subheader("คลัง PDF หลักฐานในเครื่อง")
    st.caption("ไฟล์ใน data/source_pdfs จะถูกตรวจ hash และนำเข้าอัตโนมัติเมื่อเปิดเว็บ ไฟล์เดิมจะไม่ถูกนำเข้าซ้ำและไม่เขียนทับข้อมูลเดิม")
    overview = pdf_overview(DB)
    cards = st.columns(4)
    cards[0].metric("เอกสารที่นำเข้า", overview["documents"])
    cards[1].metric("หน้าที่เก็บข้อความ", overview["pages"])
    cards[2].metric("หลักฐานดิบ", overview["extractions"])
    cards[3].metric("รายการรอตรวจ", overview["reviews"])
    if st.button("ตรวจ PDF ใหม่อีกครั้ง", key="rescan_pdfs"):
        with st.spinner("กำลังตรวจ PDF ..."):
            st.session_state["pdf_import_result"] = sync_local_pdfs(DB, PDF_DIR)
        st.rerun()
    st.markdown(f"**โฟลเดอร์ PDF:** `{Path(PDF_DIR).as_posix()}`")
    documents = list_pdf_documents(DB)
    if documents:
        rows = []
        for item in documents:
            rows.append(
                {
                    "ไฟล์": item["filename"],
                    "ประเภท": item["document_type"],
                    "หน้า": item["page_count"],
                    "ข้อความ": item["extracted_chars"],
                    "รายการรอตรวจ": item["review_count"],
                    "สถานะ": item["status"],
                    "SHA-256": item["sha256"][:16] + "...",
                    "นำเข้าเมื่อ": format_thai_stamp(item["imported_at"]),
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.warning("ยังไม่พบ PDF ในโฟลเดอร์ data/source_pdfs")
    reviews = list_pdf_review_items(DB)
    if reviews:
        st.subheader("รายการที่ต้องตรวจสอบก่อนนำไปใช้คำนวณ")
        st.warning("ระบบเก็บหลักฐานไว้แล้ว แต่ยังไม่เดาตัวเลขหรือเขตจากแถวที่อ่านซับซ้อน")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ไฟล์": item["filename"],
                        "หน้า": item["page_number"] or "ทั้งไฟล์",
                        "ประเภท": item["kind"],
                        "รายละเอียด": item["message"],
                        "หลักฐาน": item["evidence"],
                    }
                    for item in reviews
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    result = st.session_state.get("pdf_import_result") or []
    if result:
        with st.expander("ผลการตรวจรอบล่าสุด"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "ไฟล์": item.filename,
                            "ประเภท": item.document_type,
                            "สถานะ": item.status,
                            "หน้า": item.pages,
                            "อักขระ": item.extracted_chars,
                            "รายการรอตรวจ": item.review_items,
                            "ข้อความ": item.message,
                        }
                        for item in result
                    ]
                ),
                width="stretch",
                hide_index=True,
            )


def _filters(snapshot: dict):
    st.sidebar.header("ตัวกรอง")
    years = snapshot["years"] or [2569]
    focus_year = st.sidebar.selectbox("ปีสอบ", years, index=len(years) - 1)
    st.sidebar.selectbox("วิชาเอก", [SUBJECT])
    st.sidebar.caption("ชุดนี้มีเฉพาะเอกภาษาอังกฤษ การคัดเลือกทั่วไป")
    org_level = st.sidebar.selectbox("สังกัดเขต", ["ทั้งหมด", "สพป.", "สพม.", "สศศ."])
    query = st.sidebar.text_input("ค้นหาเขตพื้นที่")
    data_filter = st.sidebar.selectbox(
        "สถานะข้อมูล",
        ["ทั้งหมด", "เปิดสอบ", "ไม่เปิดสอบ", "มีจำนวนผู้สมัคร", "ยังไม่มีจำนวนผู้สมัคร"],
    )
    coverage_status = st.sidebar.selectbox(
        "สถานะประกาศปี 2569",
        ["ทั้งหมด", COVERAGE_OPEN, COVERAGE_ANNOUNCED, COVERAGE_WAITING, COVERAGE_NONE, COVERAGE_UNVERIFIED, COVERAGE_SOURCE_CLOSED],
    )
    favorites_only = st.sidebar.checkbox("แสดงเฉพาะรายการโปรด")
    st.sidebar.subheader("ตัวกรองตามคู่มือ")
    exclude_zone_one = st.sidebar.checkbox(
        "ไม่เน้น สพป. เขต 1",
        help="เป็นตัวกรองแสดงผลเท่านั้น ไม่ได้แปลว่าเขต 1 ทุกแห่งไม่ดี และอาจทำให้พลาดเขตที่มีหลักฐานดีบางแห่ง",
    )
    exclude_low_history = st.sidebar.checkbox(
        "ตัดเขตที่ประวัติเรียกน้อย",
        help="ตัดเขตที่ไม่มีประวัติเรียกที่ตรวจสอบได้ หรือมีสัดส่วนเรียกบรรจุเฉลี่ยต่ำกว่า 50% ของผู้ขึ้นบัญชีในปีที่มีข้อมูล",
    )
    st.sidebar.caption("ตัวกรองนี้ไม่ลบข้อมูลดิบ และปิดได้เพื่อกลับไปดูทุกเขต")
    names = {area.area_name: area.area_key for area in snapshot["all_areas"]}
    current = [name for name, key in names.items() if key in snapshot["favorites"]]
    picked = st.sidebar.multiselect("เขตที่สนใจ", list(names), default=current)
    picked_keys = [names[name] for name in picked]
    if names and set(picked_keys) != set(snapshot["favorites"]):
        with connect(DB) as conn:
            set_pref(conn, "favorite_area_keys", json.dumps(picked_keys, ensure_ascii=False))
    hours = st.sidebar.number_input("รอบอัปเดตเมื่อเปิดแอป (ชั่วโมง)", min_value=1, max_value=168, value=int(snapshot["interval_hours"]))
    if st.sidebar.button("บันทึกรอบอัปเดต"):
        with connect(DB) as conn:
            set_pref(conn, "refresh_hours", str(int(hours)))
        st.sidebar.success("บันทึกรอบอัปเดตแล้ว")
    st.sidebar.caption("แอปจะดึงใหม่เมื่อเปิดใช้งานหากข้อมูลเก่ากว่าช่วงนี้ การปิดแอปแล้วจะไม่ดึงเอง")
    st.sidebar.header("น้ำหนักคะแนน")
    st.sidebar.caption("การขาดครูจำกัดที่ไม่เกิน 20 จาก 100 ช่องที่ไม่ได้ปรับจะใช้ค่าเริ่มต้น")
    edited = {}
    labels = {
        "openings": "ตำแหน่งเปิดรับ",
        "competition": "การแข่งขัน",
        "call_ratio": "ประวัติเรียกบรรจุ",
        "shortage": "การขาดครู",
        "staffing_trend": "แนวโน้มอัตรากำลัง",
        "completeness": "ความครบของข้อมูล",
        "announcement": "สถานะประกาศ",
    }
    for key, label in labels.items():
        ceiling = 20 if key == "shortage" else 80
        edited[key] = st.sidebar.slider(label, 0, ceiling, int(round(snapshot["weights"][key])), key=f"weight_{key}")
    if st.sidebar.button("บันทึกน้ำหนัก"):
        with connect(DB) as conn:
            set_pref(conn, "score_weights", json.dumps(normalize_weights(edited)))
        st.sidebar.success("บันทึกน้ำหนักแล้ว")
    return (
        int(focus_year),
        org_level,
        query,
        favorites_only,
        data_filter,
        coverage_status,
        normalize_weights(edited),
        exclude_zone_one,
        exclude_low_history,
    )


def _metrics(view: dict, scores: list) -> None:
    st.subheader("ภาพรวมที่เห็นตอนนี้")
    st.caption("ตัวเลขด้านล่างเปลี่ยนตามตัวกรองด้านซ้าย และใช้เฉพาะข้อมูลที่มีอยู่จริง")
    year = view["focus_year"]
    opened = []
    for area in view["areas"]:
        stat = area.years.get(year) if year else None
        if stat and stat.opened and stat.openings:
            opened.append(stat)
    openings = sum(stat.openings or 0 for stat in opened)
    known = [stat for stat in opened if stat.applicants_known and stat.applicants is not None]
    if opened and len(known) == len(opened) and openings:
        ratio = f"1 : {sum(stat.applicants for stat in known) / openings:.1f}"
    else:
        ratio = MISSING
    ranked = sum(1 for item in scores if item.total is not None)
    columns = st.columns(4)
    columns[0].metric("เขตในตัวกรองที่เปิดสอบ", f"{len(opened)}")
    columns[1].metric(f"ตำแหน่งเปิดรับปี {year or '-'}", f"{openings:,}" if year else MISSING)
    columns[2].metric("อัตราการแข่งขันรวม", ratio)
    columns[3].metric("เขตที่ให้คะแนนเปรียบเทียบได้", f"{ranked}")
    st.caption("อัตราการแข่งขันรวมจะแสดงเมื่อทุกเขตที่เปิดสอบในปีที่เลือกมีจำนวนผู้สมัคร หากยังไม่ครบจะไม่เติมค่าเอง")
    _year_note(view)


def _year_note(view: dict) -> None:
    summary = next((item for item in view["summaries"] if item["exam_year"] == 2569), None)
    if summary is None:
        return
    counted = 0
    for area in view["all_areas"]:
        stat = area.years.get(2569)
        if stat and stat.opened and stat.openings:
            counted += stat.openings
    stamp = format_thai_stamp(view["reference_at"]) if view["reference_at"] else MISSING
    st.markdown(
        f"ยอดตำแหน่งเอกภาษาอังกฤษปี 2569 บนหน้ารายวิชาคือ **{summary['openings']}** "
        f"และผลรวมแถวเขตที่ประกาศแล้วคือ **{counted}** วันที่อ้างอิงของหน้าต้นทางคือ **{stamp}**"
    )
    st.caption(summary["note"] or "")
    st.caption(f"แหล่งตัวเลขนี้: {REPORT_URL} เวลานี้เป็นวันที่ข้อมูลบนหน้า ไม่ใช่วันที่ประกาศของแต่ละเขต และไม่ยืนยันว่าจะไม่มีการประกาศเพิ่มหลังจากนั้น")
    counts: dict[str, int] = {}
    for row in view["coverage_all"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    if counts:
        text = " · ".join(f"{name} {amount}" for name, amount in counts.items())
        st.caption("สถานะที่ตรวจจากรายชื่อบนหน้าต้นทาง: " + text)


def _compare_tab(view: dict, scores: list) -> None:
    st.subheader("เขตที่อยู่ในชุดเปรียบเทียบ")
    st.caption("เริ่มจากมุมมองการ์ดเพื่ออ่านสรุปสั้น ๆ หากต้องการตรวจตัวเลขทุกช่องให้เลือกตารางละเอียด")
    _summary_panel(view)
    by_key = {item.area_key: item for item in scores}
    rows = []
    year = view["focus_year"]
    for area in view["areas"]:
        stat = area.years.get(year) if year else None
        score = by_key.get(area.area_key)
        if stat is None:
            openings = MISSING
            applicants = MISSING
            ratio = MISSING
            listed = appointed = remaining = MISSING
        elif stat.opened is False:
            openings = NOT_OPEN
            applicants = listed = appointed = remaining = ratio = NOT_OPEN
        else:
            openings = _num(stat.openings)
            applicants = _num(stat.applicants) if stat.applicants_known else (NOT_FOUND if year == 2569 else MISSING)
            ratio = competition_text(stat.applicants, stat.openings, stat.applicants_known)
            if year == 2569 and ratio == MISSING:
                ratio = NOT_FOUND
            listed = _num(stat.listed_count)
            appointed = _num(stat.appointed_count)
            remaining = _num(stat.remaining_count)
        rows.append(
            {
                "เขตพื้นที่": area.area_name,
                "สถานะประกาศ": area.coverage_status or MISSING,
                "สังกัด": area.org_level,
                "โปรด": "ใช่" if area.area_key in view["favorites"] else "",
                "เปิดรับ": openings,
                "ผู้สมัคร": applicants,
                "อัตราการแข่งขัน": ratio,
                "ขึ้นบัญชี": listed,
                "เรียกบรรจุ": appointed,
                "คงเหลือ": remaining,
                "ประวัติเรียกบรรจุ": _component_text(score, "call_ratio"),
                "ครูจริง": _num(area.staffing_actual),
                "ครูตามเกณฑ์": _num(area.staffing_standard),
                "ผลต่างอัตรากำลัง": _gap_text(area),
                "ความครบ": _component_text(score, "completeness"),
                "ความมั่นใจ": score.confidence if score and score.total is not None else MISSING,
                "คะแนน": f"{score.total:.1f}" if score and score.total is not None else MISSING,
                "เหตุผล": score.summary if score else MISSING,
                "แหล่งตัวเลข": area.source_url or "",
                "หน้ารายเขต": area.detail_url or "",
                "เว็บเขต": area.area_website or "",
            }
        )
    frame = pd.DataFrame(rows)
    mode = st.radio(
        "รูปแบบการแสดงผล",
        ["การ์ดอ่านง่าย", "ตารางละเอียด"],
        horizontal=True,
        key="compare_view_mode",
        help="การ์ดเหมาะกับมือถือและการอ่านเร็ว ตารางเหมาะกับการตรวจหลักฐานหลายคอลัมน์",
    )
    if mode == "การ์ดอ่านง่าย":
        _area_card_view(rows)
    else:
        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
            column_config={
                "แหล่งตัวเลข": st.column_config.LinkColumn("แหล่งตัวเลข"),
                "หน้ารายเขต": st.column_config.LinkColumn("หน้ารายเขต"),
                "เว็บเขต": st.column_config.LinkColumn("เว็บเขต"),
            },
        )
    _coverage_table(view)
    st.download_button(
        "ดาวน์โหลดตารางนี้ (CSV)",
        frame.to_csv(index=False).encode("utf-8-sig"),
        file_name="teacher_exam_radar.csv",
        mime="text/csv",
    )


def _area_card_view(rows: list[dict]) -> None:
    if not rows:
        st.info("ยังไม่มีเขตในชุดเปรียบเทียบ")
        return
    for row in rows:
        with st.container(border=True):
            title_left, title_right = st.columns([3, 1])
            with title_left:
                st.markdown(f'<div class="area-card-title">{row["เขตพื้นที่"]}</div>', unsafe_allow_html=True)
                st.caption(f'{row["สังกัด"]} · สถานะประกาศ: {row["สถานะประกาศ"]}')
            with title_right:
                st.markdown(f'<div class="area-card-score">{row["คะแนน"]}</div><div class="area-card-label">คะแนน</div>', unsafe_allow_html=True)
            stats = st.columns(4)
            stats[0].metric("เปิดรับ", row["เปิดรับ"])
            stats[1].metric("ผู้สมัคร", row["ผู้สมัคร"])
            stats[2].metric("แข่งขัน", row["อัตราการแข่งขัน"])
            stats[3].metric("เรียกบรรจุ", row["เรียกบรรจุ"])
            st.caption(f'ความมั่นใจ: {row["ความมั่นใจ"]} · ความครบข้อมูล: {row["ความครบ"]}')
            with st.expander("ดูเหตุผลและข้อมูลที่เกี่ยวข้อง"):
                st.write(row["เหตุผล"])
                st.write(f'ขึ้นบัญชี: {row["ขึ้นบัญชี"]} · คงเหลือ: {row["คงเหลือ"]}')
                st.write(f'ครูจริง: {row["ครูจริง"]} · เกณฑ์: {row["ครูตามเกณฑ์"]} · ผลต่าง: {row["ผลต่างอัตรากำลัง"]}')
                links = [url for url in (row["แหล่งตัวเลข"], row["หน้ารายเขต"], row["เว็บเขต"]) if url]
                if links:
                    st.markdown("แหล่งข้อมูล: " + " · ".join(f"[{index}]({url})" for index, url in enumerate(links, start=1)))


def _summary_panel(view: dict) -> None:
    if not view["summaries"]:
        return
    with st.expander("ดูภาพรวมวิชาเอกทั้งประเทศ", expanded=False):
        st.caption("ตัวเลขนี้รวมทุกเขตที่เปิดสอบวิชาเอกในปีนั้น จากหน้าเดียวกัน คนละชุดกับตารางเขตด้านล่างซึ่งเป็นเขตที่อยู่ในหน้ารายปีปัจจุบัน")
        rows = []
        for item in view["summaries"]:
            rows.append(
                {
                    "ปี": item["exam_year"],
                    "เขตที่เปิด": _num(item["areas_opened"]),
                    "ตำแหน่ง": _num(item["openings"]),
                    "ผู้สมัคร": _num(item["applicants"]) if item["applicants_known"] else MISSING,
                    "ขึ้นบัญชี": _num(item["listed_count"]),
                    "เรียกบรรจุ": _num(item["appointed_count"]),
                    "คงเหลือ": _num(item["remaining_count"]),
                    "หมายเหตุ": item["note"] or "",
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _decision(view: dict, scores: list) -> None:
    ranked = [item for item in scores if item.total is not None]
    if not ranked:
        st.warning("ยังไม่มีเขตที่ให้คะแนนได้จากจำนวนผู้สมัครหรือประวัติเรียกบรรจุ")
        return
    pick = ranked[0]
    area = next(item for item in view["areas"] if item.area_key == pick.area_key)
    notes = choice_notes(area, pick, view["focus_year"])
    st.subheader("คำแนะนำเบื้องต้นจากข้อมูลปัจจุบัน")
    st.markdown(f"### {pick.area_name}")
    st.caption(f"อันดับ {pick.rank} · คะแนนความน่าสนใจ {pick.total:.1f} · ความมั่นใจของข้อมูล {pick.confidence} อันดับนี้ไม่ใช่โอกาสสอบติดหรือโอกาสถูกเรียกบรรจุ")
    notice = evidence_notice(scores)
    if notice:
        st.warning(notice)
    missing = [component.label for component in pick.components if component.score is None]
    if missing:
        st.warning("ข้อมูลที่ยังขาดของเขตนี้: " + ", ".join(missing))
    source = area.staffing_source_url or area.detail_url
    if source:
        st.link_button("เปิดแหล่งอัตรากำลังของเขตนี้", source)
    left, right = st.columns(2)
    left.markdown("**ข้อดี**")
    for item in notes["pros"]:
        left.write(item)
    right.markdown("**ความเสี่ยง**")
    for item in notes["risks"]:
        right.write(item)
    backups = ranked[1:4]
    if backups:
        st.markdown("**เขตสำรอง 3 อันดับจากข้อมูลชุดเดียวกัน**")
        st.dataframe(
            pd.DataFrame([{"อันดับ": item.rank, "เขต": item.area_name, "คะแนน": f"{item.total:.1f}", "ความมั่นใจ": item.confidence, "เหตุผล": item.summary} for item in backups]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("มีเขตที่ให้คะแนนได้ไม่ถึง 4 เขต จึงยังไม่มีเขตสำรองครบ 3 อันดับ")


def _guide_panel(view: dict, scores: list) -> None:
    st.subheader("ตารางหลักฐานเขตที่เปิดเอกภาษาอังกฤษปี 2569")
    review = evidence_review(view["all_areas"], view["all_areas"], view["focus_year"], view["account_events"])
    st.warning("ตารางหลักฐานไม่ใช่โอกาสสอบติดหรือโอกาสถูกบรรจุ และไม่ได้เรียงเขตตามสัดส่วนเรียกบรรจุ")
    st.caption(review["ratio_note"])
    st.caption("แถวที่เทียบกันได้ต้องใช้นิยามนี้เท่านั้น: " + review["comparable_definition"])
    _decision_report(review["decision"])
    ranked = [item for item in scores if item.total is not None]
    if ranked:
        st.caption(
            f"คะแนนความน่าสนใจเดิมอยู่คนละส่วนและยังนำโดย {ranked[0].area_name} ที่ {ranked[0].total:.1f} "
            "สูตรนี้ไม่ได้ถูกเปลี่ยน"
        )
    _evidence_table("เขตที่มีหลักฐานเพียงพอ", review["sufficient"], "ยังไม่มีเขตในกลุ่มนี้ เพราะยังไม่มีจำนวนผู้สมัครปี 2569")
    _evidence_table(
        "เขตที่น่าสนใจแต่ข้อมูลยังไม่ครบ",
        review["incomplete"],
        "มีหน้ารวบรวมปี 2567 ที่ยังไม่พบข้อขัดแย้ง แต่จำนวนผู้สมัครปี 2569 ยังเป็นรอข้อมูล",
    )
    _evidence_table(
        "เขตที่ต้องรอข้อมูลหรือมีข้อมูลขัดแย้ง",
        review["wait"],
        "ยังไม่มีหน้ารวบรวมปี 2567 ที่ใช้ได้ หรือมีตัวเลขที่ไม่ลงสมการ หรือมีจำนวนเรียกบรรจุที่แหล่งไม่ตรงกัน",
    )
    _watch_table(review["watch"])


def _evidence_frame(rows: list[dict]) -> list[dict]:
    frame = []
    for item in rows:
        recruit = item["recruitment_by_year"]
        balance = item["balance_by_year"]
        frame.append(
            {
                "เขต": item["area_name"],
                "เปิดอังกฤษ 2569": item["openings"],
                "ผู้สมัคร 2569": item["applicants"],
                "หน้ารวบรวม 2566": recruit[2566],
                "หน้ารวบรวม 2567": recruit[2567],
                "หน้ารวบรวม 2568": recruit[2568],
                "บัญชีคงเหลือ 2566": balance[2566],
                "บัญชีคงเหลือ 2567": balance[2567],
                "บัญชีคงเหลือ 2568": balance[2568],
                "สัดส่วนแยกปีและแหล่ง": item["share_text"],
                "ตัวตั้ง": item["numerator"] if item["numerator"] is not None else MISSING,
                "ตัวหาร": item["denominator"] if item["denominator"] is not None else MISSING,
                "ความน่าเชื่อถือ": item["reliability"],
                "เทียบในนิยามเดียวกันได้": "ได้" if item["comparable"] else "ไม่ได้",
                "ข้อขัดแย้ง": " ; ".join(item["conflicts"]) if item["conflicts"] else "ไม่พบ",
                "ข้อมูลที่ขาด": " ; ".join(item["missing"]) if item["missing"] else "ไม่พบ",
                "วันที่บนหน้ารวบรวม": item["recruitment_date"],
                "วันที่ในเอกสารบัญชี": item["balance_announced"],
                "แหล่งหน้ารวบรวม": item["recruitment_source"],
                "แหล่งบัญชี": item["balance_source"],
            }
        )
    return frame


def _evidence_table(title: str, rows: list[dict], empty_note: str) -> None:
    st.markdown(f"**{title}**")
    if not rows:
        st.caption(empty_note)
        return
    st.caption(empty_note)
    st.dataframe(
        pd.DataFrame(_evidence_frame(rows)),
        width="stretch",
        hide_index=True,
        column_config={"แหล่งหน้ารวบรวม": st.column_config.LinkColumn("แหล่งหน้ารวบรวม")},
    )


def _decision_report(decision: dict) -> None:
    st.subheader("อันดับเบื้องต้นสำหรับเลือก 1 เขต")
    st.info(decision["note"])
    st.caption("เขตที่ไม่ได้เป็นตัวเลือกหลักยังอยู่ในตารางด้านล่าง พร้อมเหตุผล ข้อมูลว่างไม่ได้ถูกใส่เป็นศูนย์ และไม่ได้สร้างจำนวนผู้สมัครหรือโอกาสบรรจุ")
    primary = decision["primary"]
    if len(primary) == 1:
        item = primary[0]
        st.markdown(f"**ตัวเลือกหลักเบื้องต้น:** {item['area_name']}")
        st.markdown("**ข้อมูลจริง:** " + item["facts"])
        st.markdown("**ข้อสันนิษฐาน:** " + " ".join(item["assumptions"]))
        st.markdown("**ข้อมูลยังไม่มี:** " + (" ; ".join(item["missing"]) if item["missing"] else "ไม่พบ"))
    elif len(primary) > 1:
        st.warning("มีหลายเขตที่หลักฐานปีเดียวกันตรงกัน จึงยังไม่ยุบเหลือเขตเดียวด้วยจำนวนอัตราที่เปิด")
    else:
        st.warning("ยังไม่มีเขตที่หน้ารวบรวมและบัญชีคงเหลือปี 2567 ตรงกัน")
    st.markdown("**ข้อมูลที่อาจทำให้ผลเลือกเขตเปลี่ยน**")
    for line in decision["change_if"]:
        st.markdown(f"- {line}")
    _decision_table(
        "ตารางเปรียบเทียบเอกภาษาอังกฤษ ทุกเขตที่เปิดปี 2569",
        decision["rows"],
        "ยังไม่มีเขตที่เปิดเอกภาษาอังกฤษในปีที่เลือก",
    )


def _decision_table(title: str, rows: list[dict], empty_note: str) -> None:
    st.markdown(f"**{title}**")
    if not rows:
        st.caption(empty_note)
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "เขต": item["area_name"],
                    "สถานะ": item["status"],
                    "เปิดอังกฤษ 2569": item["openings"],
                    "ผู้สมัคร 2569": item["applicants"],
                    "หน้ารวบรวม 2566": item["recruitment_by_year"][2566],
                    "หน้ารวบรวม 2567": item["recruitment_by_year"][2567],
                    "หน้ารวบรวม 2568": item["recruitment_by_year"][2568],
                    "บัญชีคงเหลือ 2566": item["balance_by_year"][2566],
                    "บัญชีคงเหลือ 2567": item["balance_by_year"][2567],
                    "บัญชีคงเหลือ 2568": item["balance_by_year"][2568],
                    "ยอดทุกวิชา": "ไม่ได้ใช้ ไฟล์เกษียณเป็นยอดทุกวิชา ไม่ใช่อัตราเอกอังกฤษ",
                    "ความครบถ้วน": item["reliability"],
                    "ข้อมูลจริง": item["facts"],
                    "ข้อสันนิษฐาน": " ".join(item["assumptions"]),
                    "ข้อมูลยังไม่มี": " ; ".join(item["missing"]) if item["missing"] else "ไม่พบ",
                    "เหตุผล": item["reason"],
                    "แหล่งหน้ารวบรวม": item["recruitment_source"],
                }
                for item in rows
            ]
        ),
        width="stretch",
        hide_index=True,
        column_config={"แหล่งหน้ารวบรวม": st.column_config.LinkColumn("แหล่งหน้ารวบรวม")},
    )


def _watch_table(rows: list[dict]) -> None:
    st.markdown("**เขตที่ควรจับตา**")
    st.caption("เรียงตามชื่อเขต ไม่ได้เรียงตามสัดส่วนเรียกบรรจุ ข้อเท็จจริงกับข้อสันนิษฐานอยู่คนละช่อง")
    if not rows:
        st.caption(MISSING)
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "เขต": item["area_name"],
                    "ข้อเท็จจริง": " ".join(item["facts"]),
                    "ข้อสันนิษฐาน": " ".join(item["assumptions"]),
                    "แหล่ง": item["source"],
                }
                for item in rows
            ]
        ),
        width="stretch",
        hide_index=True,
        column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")},
    )


def _account_table(view: dict) -> None:
    st.subheader("บัญชีคงเหลือเอกภาษาอังกฤษ")
    st.caption("ตารางนี้แยกจากประวัติเรียกบรรจุของรอบสอบทั่วไป ไม่ใช่ตำแหน่งปี 2569 และไม่ใช่ลำดับที่เรียกถึง ช่องว่างไม่ได้ถูกใส่เป็นศูนย์")
    rows = []
    for item in view["account_events"]:
        rows.append(
            {
                "ปีบัญชี": item["exam_year"],
                "เขต": item["area_name"],
                "เรียกบรรจุ": _num(item["appointed_count"]),
                "คงเหลือ": _num(item["remaining_count"]),
                "ลำดับที่เรียกถึง": MISSING if item["call_rank_reached"] is None else item["call_rank_reached"],
                "หลักฐาน": item["evidence"],
                "แหล่ง": item["source_url"],
            }
        )
    if not rows:
        st.caption(NOT_FOUND)
        return
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")})


def _strategy_tab(view: dict, scores: list) -> None:
    _guide_panel(view, scores)
    st.info("แต่ละกลุ่มใช้เฉพาะข้อมูลที่มีหลักฐาน เขตที่ขาดครูแต่ไม่มีประกาศรับจะไม่ถูกยกเป็นตัวเลือกหลัก")
    groups = classify_choices(view["areas"], scores, view["coverage"], view["focus_year"])
    titles = {
        "A": "A. ตัวเลือกน่าสนใจ จากข้อมูลสอบที่ครบพอให้เทียบ",
        "B": "B. ตัวเลือกสำรอง ที่ยังขาดผู้สมัครหรือประวัติบางส่วน",
        "C": "C. เขตที่ต้องติดตามประกาศเพิ่ม",
        "D": "D. ข้อมูลไม่พอสำหรับการตัดสินใจ",
    }
    for key, title in titles.items():
        st.subheader(title)
        rows = groups[key]
        if not rows:
            st.caption(MISSING)
            continue
        frame = pd.DataFrame(rows[:40])
        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
            column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")},
        )
        if len(rows) > 40:
            st.caption(f"แสดง 40 จาก {len(rows)} เขต")
    _staffing_chart(view)


def _staffing_chart(view: dict) -> None:
    st.subheader("ตารางอัตรากำลังครูภาษาอังกฤษ")
    rows = []
    for item in view["staffing_rows"]:
        metrics = staffing_metrics(item["actual_count"], item["standard_count"])
        rows.append(
            {
                "เขต": item["area_name"],
                "ครูที่มีจริง": _num(item["actual_count"]),
                "เกณฑ์": _num(item["standard_count"]),
                "ผลต่าง": f"{metrics['gap']:+d}" if metrics["gap"] is not None else MISSING,
                "เทียบเกณฑ์": f"{metrics['fill_percent']:.1f}%" if metrics["fill_percent"] is not None else MISSING,
                "ระดับ": metrics["level"],
                "วันที่อ้างอิง": format_thai_stamp(item["reference_at"]) if item["reference_at"] else MISSING,
                "การตรวจ": item["verify_status"] or MISSING,
                "แหล่ง": item["source_url"] or "",
            }
        )
    if not rows:
        st.warning(MISSING)
        return
    table = pd.DataFrame(rows)
    st.dataframe(table, width="stretch", hide_index=True, column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")})
    st.subheader("ครูที่มีจริงเทียบกับเกณฑ์")
    chart_rows = []
    for item in view["staffing_rows"]:
        if item["actual_count"] is None or item["standard_count"] in (None, 0):
            continue
        chart_rows.append({"เขต": item["area_name"], "ครูที่มีจริง": item["actual_count"], "เกณฑ์": item["standard_count"]})
    if not chart_rows:
        st.warning("ยังไม่มีเขตที่คำนวณกราฟได้")
        return
    frame = pd.DataFrame(chart_rows)
    st.caption("ค่าในกราฟเป็นจำนวนคนจากหน้ารายเขต ไม่ใช่จำนวนตำแหน่งสอบ หน้า HRMS มีเฉพาะยอดภาษาอังกฤษทั้งประเทศ จึงไม่ถูกนำมาวาดเป็นกราฟรายเขต")
    st.bar_chart(frame, x="เขต", y=["ครูที่มีจริง", "เกณฑ์"], width="stretch")
    dated = next((item["reference_at"] for item in view["staffing_rows"] if item["reference_at"]), None)
    st.caption(f"วันที่อ้างอิงอัตรากำลัง: {format_thai_stamp(dated) if dated else MISSING}")
    conflict = [item for item in view["warnings"]]
    if conflict:
        st.warning("มีคำเตือนจากการตรวจข้อมูล " + str(len(conflict)) + " รายการ ดูรายละเอียดในแท็บแหล่งข้อมูล")


def _gap_text(area) -> str:
    metrics = staffing_metrics(area.staffing_actual, area.staffing_standard)
    if metrics["gap"] is None:
        return MISSING
    return f"{metrics['gap']:+d}"


def _component_text(score, key: str) -> str:
    if score is None:
        return MISSING
    component = next((item for item in score.components if item.key == key), None)
    if component is None or component.score is None:
        return MISSING
    return f"{component.score:.0f}"


def _coverage_table(view: dict) -> None:
    if not view["coverage"]:
        return
    st.subheader("ความครบของประกาศปี 2569")
    st.caption("เขตที่ไม่มีแถวเอกภาษาอังกฤษไม่ได้ถูกตีความว่าไม่เปิดสอบ ช่องวันที่ประกาศจะว่างจนกว่าจะพบวันที่บนประกาศของเขต")
    rows = []
    for item in view["coverage"]:
        rows.append(
            {
                "เขตพื้นที่": item["area_name"],
                "สถานะ": item["status"],
                "วันที่ประกาศ": format_thai_stamp(item["announced_at"]) if item["announced_at"] else MISSING,
                "หลักฐาน": item["evidence"],
                "แหล่ง": item["source_url"] or "",
            }
        )
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")},
    )


def _rank_tab(scores: list) -> None:
    st.info("อันดับนี้เทียบจากข้อมูลที่มีในตัวกรองปัจจุบันเท่านั้น ไม่ได้แปลว่าอันดับหนึ่งมีโอกาสสอบติดหรือถูกเรียกบรรจุสูงที่สุด เขตที่ควรติดตามประกาศเพิ่มอยู่ในตารางความครบของประกาศปี 2569 ในแท็บเปรียบเทียบเขต")
    st.caption("สูตรคะแนนความน่าสนใจชุดนี้ไม่ได้ถูกแทนที่ด้วยคู่มือเลือกเขต เพราะคู่มือไม่ได้กำหนดน้ำหนัก บัญชีคงเหลือไม่ถูกนำเข้าสูตรนี้ รายการตามคู่มืออยู่ในแท็บกลยุทธ์เลือกเขต")
    st.caption(formula_text())
    ready = [item for item in scores if item.total is not None and item.confidence == CONFIDENCE_READY]
    partial = [item for item in scores if item.total is not None and item.confidence != CONFIDENCE_READY]
    pending = [item for item in scores if item.total is None]
    _score_group("เทียบได้จากข้อมูลที่ครบตามสูตร", ready)
    _score_group("มีข้อมูลบางส่วน คะแนนไม่ได้คิดเหมือนข้อมูลครบ", partial)
    if pending:
        st.subheader("ข้อมูลไม่เพียงพอสำหรับจัดอันดับ")
        st.dataframe(
            pd.DataFrame([{"เขต": item.area_name, "สาเหตุ": item.summary} for item in pending]),
            width="stretch",
            hide_index=True,
        )


def _score_group(title: str, scores: list) -> None:
    st.subheader(title)
    if not scores:
        st.caption(MISSING)
        return
    for item in scores[:15]:
        with st.expander(f"อันดับ {item.rank} · {item.area_name} · {item.total:.1f} · ความมั่นใจ{item.confidence}"):
            for component in item.components:
                value = f"{component.score:.1f}" if component.score is not None else MISSING
                st.markdown(f"**{component.label}:** {value} จากน้ำหนัก {component.weight:.0f}")
                st.write(component.reason)


def _picked_tab(view: dict, scores: list) -> None:
    st.caption("เลือก 3 ถึง 5 เขตเพื่อเทียบหลักฐานที่มีอยู่ คะแนนความน่าสนใจไม่ใช่โอกาสสอบติด และแยกจากความมั่นใจของข้อมูล")
    areas = view["areas"]
    if len(areas) < 3:
        st.warning("เขตในตัวกรองปัจจุบันมีไม่ถึง 3 เขต")
        return
    names = [area.area_name for area in areas]
    selected = st.multiselect("เขตเป้าหมาย", names, max_selections=5, placeholder="เลือก 3 ถึง 5 เขต")
    if len(selected) < 3:
        st.info("เลือกอย่างน้อย 3 เขต")
        return
    chosen = [area for area in areas if area.area_name in selected]
    by_key = {item.area_key: item for item in scores}
    rows = []
    for area in chosen:
        stat = area.years.get(view["focus_year"])
        score = by_key.get(area.area_key)
        metrics = staffing_metrics(area.staffing_actual, area.staffing_standard)
        notes = choice_notes(area, score, view["focus_year"])
        rows.append(
            {
                "เขตพื้นที่": area.area_name,
                "ตำแหน่ง": _num(stat.openings) if stat and stat.opened else NOT_FOUND,
                "ผู้สมัคร": _num(stat.applicants) if stat and stat.applicants_known else NOT_FOUND,
                "อัตรา": competition_text(stat.applicants, stat.openings, stat.applicants_known) if stat else NOT_FOUND,
                "เรียกบรรจุ": _component_text(score, "call_ratio"),
                "ผลต่างอัตรากำลัง": f"{metrics['gap']:+d}" if metrics["gap"] is not None else NOT_FOUND,
                "คะแนนความน่าสนใจ": f"{score.total:.1f}" if score and score.total is not None else NOT_FOUND,
                "ความมั่นใจ": score.confidence if score and score.total is not None else NOT_FOUND,
                "ข้อดี": " ".join(notes["pros"]),
                "ข้อเสียและความเสี่ยง": " ".join(notes["risks"]),
                "ข้อมูลที่ยังขาด": " ".join(decision_gaps(area, view["focus_year"])),
                "แหล่ง": area.detail_url or area.staffing_source_url or "",
            }
        )
    if view["focus_year"] == 2569:
        for row in rows:
            if row["อัตรา"] == MISSING:
                row["อัตรา"] = NOT_FOUND
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")})
    st.subheader("ถ้าข้อมูลที่ยังขาดเปลี่ยน")
    if not any(area.years.get(view["focus_year"]) and area.years[view["focus_year"]].applicants_known for area in chosen):
        st.info("เขตที่เลือกยังไม่มีจำนวนผู้สมัครจริง จึงยังจำลองอัตราการแข่งขันไม่ได้ และไม่ได้ใส่จำนวนสมมติลงฐานข้อมูล เมื่อมีตัวเลขผู้สมัครจากประกาศ อันดับในสูตรการแข่งขันอาจเปลี่ยน")
    else:
        percent = st.slider("สมมติว่าผู้สมัครของชุดนี้เพิ่ม (%)", 0, 100, 20, key="picked_applicants")
        result = applicant_increase(chosen, view["focus_year"], percent, chosen[0].area_key)
        _show_scenario(result)
    call_result = future_calls(chosen)
    st.markdown("**ประวัติเรียกบรรจุ กรณีต่ำ กลาง สูง จากตัวเลขจริง**")
    _show_scenario(call_result)


def _history_tab(view: dict) -> None:
    st.caption("จำนวนผู้ได้รับการเรียกบรรจุมาจากตัวเลขบรรจุเท่านั้น ไม่ได้นำจำนวนตำแหน่งเปิดรับมาใส่แทน ลำดับบัญชีและวันที่ประกาศเรียกจะขึ้นเมื่อมีประกาศที่อ่านได้")
    events = {(item["area_key"], item["exam_year"]): item for item in view["call_events"]}
    rows = []
    for area in view["areas"]:
        for year, stat in sorted(area.years.items()):
            event = events.get((area.area_key, year))
            if stat.opened is False:
                rows.append(
                    {
                        "ปีสอบ": year,
                        "เขตพื้นที่": area.area_name,
                        "วิชาเอก": SUBJECT,
                        "ลำดับบัญชีที่เรียกถึง": NOT_OPEN,
                        "จำนวนผู้ได้รับการเรียกบรรจุ": NOT_OPEN,
                        "วันที่ประกาศเรียก": NOT_OPEN,
                        "จำนวนคนคงเหลือ": NOT_OPEN,
                        "แหล่ง": area.source_url or "",
                    }
                )
                continue
            rows.append(
                {
                    "ปีสอบ": year,
                    "เขตพื้นที่": area.area_name,
                    "วิชาเอก": SUBJECT,
                    "ลำดับบัญชีที่เรียกถึง": _num(event["call_rank_reached"]) if event and event["call_rank_reached"] is not None else MISSING,
                    "จำนวนผู้ได้รับการเรียกบรรจุ": _num(event["appointed_count"]) if event and event["appointed_count"] is not None else _num(stat.appointed_count),
                    "วันที่ประกาศเรียก": format_thai_stamp(event["announced_at"]) if event and event["announced_at"] else MISSING,
                    "จำนวนคนคงเหลือ": _num(event["remaining_count"]) if event and event["remaining_count"] is not None else _num(stat.remaining_count),
                    "แหล่ง": (event or {}).get("source_url") or area.source_url or "",
                }
            )
    if rows:
        st.dataframe(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
            column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")},
        )
    else:
        st.info("ยังไม่มีประวัติในตัวกรองนี้")
    _account_table(view)


def _scenario_tab(view: dict) -> None:
    st.info(f"ผลในแท็บนี้เป็น{SIMULATION} คำนวณจากข้อมูลในเครื่องทันที และไม่เขียนทับข้อมูลที่ดึงมา")
    areas = view["areas"]
    year = view["focus_year"]
    if not areas or year is None:
        st.warning(MISSING)
        return
    one, two, three, four = st.tabs(["ผู้สมัครเพิ่ม", "เรียกบรรจุในอนาคต", "เขตเป้าหมายเสียเปรียบ", "เรียกถึงลำดับของเรา"])
    with one:
        percent_choice = st.radio("เพิ่มผู้สมัคร", ["10%", "20%", "30%", "50%", "กำหนดเอง"], horizontal=True)
        custom = st.number_input("เปอร์เซ็นต์ที่กำหนดเอง", min_value=0.0, max_value=300.0, value=15.0, step=5.0)
        percent = custom if percent_choice == "กำหนดเอง" else float(percent_choice.strip("%"))
        target_name = st.selectbox("เขตเป้าหมายสำหรับเทียบกับเขตสำรอง", [area.area_name for area in areas], key="applicant_target")
        target = next(area for area in areas if area.area_name == target_name)
        result = applicant_increase(areas, year, percent, target.area_key)
        _show_scenario(result)
    with two:
        list_size = st.number_input("จำนวนคนในบัญชีสำหรับแปลงเป็นจำนวนคน (ไม่บังคับ)", min_value=0, value=0, step=1)
        result = future_calls(areas, int(list_size) or None)
        _show_scenario(result)
    with three:
        target_name = st.selectbox("เขตเป้าหมาย", [area.area_name for area in areas])
        target = next(area for area in areas if area.area_name == target_name)
        applicant_percent = st.slider("ผู้สมัครของเขตเป้าหมายเพิ่ม (%)", 0, 100, 20)
        use_openings = st.checkbox("กำหนดจำนวนตำแหน่งใหม่ของเขตเป้าหมาย")
        openings_value = st.number_input("ตำแหน่งเปิดรับใหม่", min_value=0, value=1, step=1)
        others = st.slider("ผู้สมัครของเขตอื่นเพิ่ม (%)", 0, 100, 0)
        result = target_disadvantage(
            areas,
            year,
            target.area_key,
            applicant_percent,
            int(openings_value) if use_openings else None,
            others,
        )
        _show_scenario(result)
    with four:
        area_name = st.selectbox("ใช้ประวัติของเขต", [area.area_name for area in areas], key="reach_area")
        area = next(item for item in areas if item.area_name == area_name)
        my_rank = st.number_input("อันดับของตนเอง", min_value=1, value=1, step=1)
        people = st.number_input("จำนวนคนในบัญชี", min_value=1, value=10, step=1)
        use_known = st.checkbox("มีจำนวนที่เรียกบรรจุแล้วและต้องการใส่เอง")
        known = st.number_input("เรียกบรรจุแล้ว (คน)", min_value=0, value=0, step=1)
        result = reach_rank(area, int(my_rank), int(people), int(known) if use_known else None)
        _show_scenario(result)
    st.subheader("เปรียบเทียบกรณีต่ำ กลาง และสูง")
    outlook_name = st.selectbox("เขตสำหรับตารางปัจจัย", [area.area_name for area in areas], key="outlook_area")
    outlook_area = next(item for item in areas if item.area_name == outlook_name)
    st.dataframe(pd.DataFrame(factor_outlook(outlook_area)).astype("string"), width="stretch", hide_index=True)
    shift = st.slider("สมมติว่าครูที่มีจริงเปลี่ยน (%)", -30, 30, 0, key="staff_shift")
    staff_result = staffing_change(areas, year, shift, view["weights"], outlook_area.area_key)
    _show_scenario(staff_result)


def _show_scenario(result) -> None:
    st.markdown(f"**สมมติฐาน:** {result.assumption}")
    st.markdown(f"**ข้อจำกัด:** {result.limitation}")
    st.write(result.message)
    if result.comparison:
        st.markdown("**เทียบกับกรณีพื้นฐาน**")
        st.dataframe(pd.DataFrame(result.comparison).astype("string"), width="stretch", hide_index=True)
    if result.rows:
        frame = pd.DataFrame(result.rows).astype("string")
        st.dataframe(frame, width="stretch", hide_index=True)


def _monitor_tab(view: dict) -> None:
    st.caption("การตรวจแหล่งนี้เกิดเมื่อเปิดแอปหรือกดรีเฟรชเท่านั้น แอปที่ปิดอยู่ไม่ได้ตามประกาศต่อ")
    docs = view.get("monitor_documents") or []
    runs = view.get("source_runs") or []
    watched = ["personnel_eoffice", "obec_assistant", "prd_teacher", "otepc_circular", "kruphuchuay", "hrms_obec"]
    rows = []
    for code in watched:
        run = next((item for item in runs if item["source_code"] == code), None)
        owned = [item for item in docs if item["source_code"] == code]
        last_ok = (view.get("source_success") or {}).get(code)
        rows.append(
            {
                "แหล่ง": code,
                "ตรวจล่าสุด": format_thai_stamp(run["finished_at"]) if run and run.get("finished_at") else NOT_FOUND,
                "ดึงสำเร็จล่าสุด": format_thai_stamp(last_ok) if last_ok else NOT_FOUND,
                "สถานะรอบล่าสุด": run["status"] if run else NOT_FOUND,
                "ประกาศที่พบในรอบล่าสุด": run["records_written"] if run else 0,
                "เอกสารที่เก็บ": len(owned),
                "อ่านแล้ว": sum(1 for item in owned if item["status"] in {"confirmed", "listed", "no_file", "no_english_figure"}),
                "เปิดไม่ได้": sum(1 for item in owned if item["status"] == "fetch_failed"),
                "รอตรวจ": sum(1 for item in owned if item["status"] in {"review", "unreadable", "mismatch"}),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    keyword = st.text_input("ค้นหาเอกสาร", placeholder="วิชา ชื่อเขต รหัสเขต หรือคำภาษาไทย")
    shown = [item for item in docs if document_matches(item, keyword)]
    if shown:
        st.subheader("เอกสารจากแหล่งทางการ")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "แหล่ง": item["source_code"],
                        "ปี": item["exam_year"] or "",
                        "ประเภท": item["exam_type"] or "",
                        "สถานะ": item["status"],
                        "บันทึก": item["message"] or "",
                        "เปิดเอกสาร": item["url"],
                    }
                    for item in shown[:40]
                ]
            ),
            width="stretch",
            hide_index=True,
            column_config={"เปิดเอกสาร": st.column_config.LinkColumn("เปิดเอกสาร")},
        )
    accounts = [item for item in (view.get("account_events") or []) if document_matches(item, keyword)]
    st.subheader("บัญชีคงเหลือเอกภาษาอังกฤษที่ชื่อเขตตรงกัน")
    st.caption("ตัวเลขนี้มาจากบัญชีที่เขตรายงาน สพฐ. ไม่ใช่ตำแหน่งสอบปี 2569 และไม่ถูกนำไปบวกในคะแนนความน่าสนใจ")
    if accounts:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "เขต": item["area_name"],
                        "ปีบัญชี": item["exam_year"],
                        "เรียกบรรจุ": item["appointed_count"],
                        "คงเหลือ": item["remaining_count"],
                        "ลำดับที่เรียกถึง": NOT_FOUND,
                        "แหล่ง": item["source_url"],
                    }
                    for item in accounts[:40]
                ]
            ),
            width="stretch",
            hide_index=True,
            column_config={"แหล่ง": st.column_config.LinkColumn("แหล่ง")},
        )
    else:
        st.info(NOT_FOUND)


def _source_tab(view: dict) -> None:
    st.subheader("แหล่งที่ดึงไม่สำเร็จหรือไม่มีตารางที่ใช้ได้")
    failed = view["failed_sources"]
    if not failed:
        st.caption("รอบล่าสุดของแต่ละแหล่งไม่มีสถานะล้มเหลว")
    else:
        st.dataframe(pd.DataFrame(failed), width="stretch", hide_index=True)
    documents = view["documents"]
    if documents:
        st.subheader("ผลการเปิดประกาศของเขต")
        frame = pd.DataFrame(documents)[["area_name", "doc_kind", "status", "announced_at", "message", "url", "observed_at"]]
        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
            column_config={"url": st.column_config.LinkColumn("url")},
        )
    st.subheader("อัตรากำลังระดับประเทศจาก HRMS")
    national = view["national_staffing"]
    if not national:
        st.warning(f"HRMS: {MISSING}")
    else:
        gap = national["gap_count"]
        st.markdown(
            f"ภาษาอังกฤษทั้งประเทศ มีครูตาม จ.18 **{national['actual_count']:,}** คน "
            f"เกณฑ์มาตรฐาน **{national['standard_count']:,}** คน ผลต่าง **{gap:+,}** คน"
        )
        if national["need_10y"] is not None:
            st.markdown(
                f"ความต้องการทดแทนสะสม 10 ปี **{national['need_10y']:,}** คน "
                f"และผู้เกษียณสะสม **{national['retire_10y']:,}** คน"
            )
        st.caption(national["reference_note"] or "")
        st.caption("ห้ามใช้จำนวนครูที่ขาดทั้งประเทศแทนจำนวนตำแหน่งที่เปิดสอบในแต่ละเขต")
        if national.get("source_url"):
            st.link_button("เปิดหน้า HRMS", national["source_url"])
    st.subheader("ลิงก์แยกสังกัดบนหน้าต้นทาง")
    for label, url in RELATED_URLS.items():
        st.link_button(label, url)
    st.subheader("ประวัติการดึงข้อมูล")
    if view["runs"]:
        st.dataframe(pd.DataFrame(view["runs"])[["source_code", "finished_at", "status", "records_written", "message"]], width="stretch", hide_index=True)
    if view["changes"]:
        st.subheader("การเปลี่ยนแปลงล่าสุด")
        st.dataframe(pd.DataFrame(view["changes"]), width="stretch", hide_index=True)
    if view["warnings"]:
        st.subheader("คำเตือนจากการตรวจข้อมูล")
        for item in view["warnings"]:
            st.caption(item["message"])
    st.subheader("นำเข้า CSV หรือ XLSX")
    st.caption("ใช้เมื่อเว็บต้นทางดึงอัตโนมัติไม่ได้ แถวที่นำเข้าจะถูกติดแหล่งว่าเป็นไฟล์ของผู้ใช้ และไม่ทับข้อมูลที่ดึงจากเว็บ")
    uploaded = st.file_uploader("เลือกไฟล์", type=["csv", "xlsx"])
    if uploaded is not None:
        records, warnings = parse_import_file(uploaded.name, uploaded.getvalue())
        for warning in warnings:
            st.warning(warning)
        if records and st.button("บันทึกไฟล์นี้"):
            outcome = import_records(DB, records)
            st.success(outcome["message"])
            st.rerun()


def _num(value) -> str:
    if value is None:
        return MISSING
    return f"{int(value):,}"


if __name__ == "__main__":
    main()
