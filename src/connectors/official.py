from __future__ import annotations

OFFICIAL_SITES = [
    {
        "code": "obec",
        "url": "https://www.obec.go.th/",
        "name": "สำนักงานคณะกรรมการการศึกษาขั้นพื้นฐาน",
    },
    {
        "code": "personnel_obec",
        "url": "https://personnel.obec.go.th/",
        "name": "กลุ่มบริหารงานบุคคล สพฐ.",
    },
    {
        "code": "otepc",
        "url": "https://www.otepc.go.th/",
        "name": "สำนักงานคณะกรรมการข้าราชการครูและบุคลากรทางการศึกษา",
    },
]


def classify_homepage(html: str) -> str:
    text = html or ""
    has_exam_table = "ภาษาอังกฤษ" in text and ("ครูผู้ช่วย" in text) and ("<table" in text.lower())
    if has_exam_table:
        return "พบคำว่าครูผู้ช่วยและภาษาอังกฤษในหน้าแรก แต่ยังไม่มีตัวแยกตารางรอบทั่วไปที่เชื่อถือได้ จึงไม่บันทึกเป็นตำแหน่งสอบ"
    return "เปิดหน้าแรกได้ แต่ไม่พบตารางสอบครูผู้ช่วยเอกภาษาอังกฤษที่ดึงอัตโนมัติได้"
