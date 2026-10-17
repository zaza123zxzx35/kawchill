from pathlib import Path

from pypdf import PdfWriter

from src.db import init_db
from src.pdf_ingest import classify_document, import_pdf, list_pdf_documents, pdf_overview


def test_pdf_document_classification_covers_the_five_document_families():
    assert classify_document("1.แนวทางวิเคราะห์เลือกเขต ครู สพฐ.2569.pdf") == "คู่มือแนวทางเลือกเขต"
    assert classify_document("2.สรุปอัตราเปิดสอบ69 -อัตราเกษียณครูผู้ช่วย 69-71 .pdf") == "อัตราเปิดสอบและเกษียณ 2569-2571"
    assert classify_document("บัญชีคงเหลือ-ครูผู้ช่วย-สังกัด-สพฐ-ปี-66.pdf") == "บัญชีคงเหลือ 2566"
    assert classify_document("บัญชีคงเหลือ-ครูผู้ช่วย-สังกัด-สพฐ-ปี-67.pdf") == "บัญชีคงเหลือ 2567"
    assert classify_document("บัญชีคงเหลือ-ครูผู้ช่วย-สังกัด-สพฐ-ปี-68.pdf") == "บัญชีคงเหลือ 2568"


def test_import_is_idempotent_and_keeps_a_page_record(tmp_path: Path):
    pdf_path = tmp_path / "บัญชีคงเหลือ-ครูผู้ช่วย-สังกัด-สพฐ-ปี-66.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    db_path = tmp_path / "radar.db"
    init_db(db_path)

    first = import_pdf(pdf_path, db_path)
    second = import_pdf(pdf_path, db_path)

    assert first.status == "นำเข้าแล้ว"
    assert second.status == "ข้ามไฟล์เดิม"
    assert pdf_overview(db_path)["documents"] == 1
    assert pdf_overview(db_path)["pages"] == 1
    assert len(list_pdf_documents(db_path)) == 1
