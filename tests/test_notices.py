from src.connectors.notices import find_notice_links, find_past_call_links, interpret_notice_text, parse_notice_page


def test_unreadable_notice_does_not_create_numbers():
    result = interpret_notice_text("%%% not thai %%%")
    assert result["readable"] is False
    assert result["openings"] is None
    assert result["applicants"] is None
    assert result["appointed_count"] is None
    assert result["call_rank_reached"] is None


def test_clear_english_line_keeps_only_that_number():
    text = "ประกาศรับสมัครครูผู้ช่วย\nภาษาอังกฤษ จำนวน 6 ตำแหน่ง\nคณิตศาสตร์ จำนวน 3 ตำแหน่ง\n"
    result = interpret_notice_text(text)
    assert result["readable"] is True
    assert result["openings"] == 6
    assert result["applicants"] is None


def test_notice_page_keeps_dates_without_inventing_counts():
    html = """
    <html><head>
      <title>รับสมัครครูผู้ช่วย</title>
      <meta name="description" content="รับสมัครครูผู้ช่วย ปี พ.ศ. 2569 ระหว่างวันที่ 19 - 25 ตุลาคม 2569">
    </head><body>
      <h1>รับสมัครสอบแข่งขัน ตำแหน่งครูผู้ช่วย ปี พ.ศ. 2569</h1>
      <span>12 ตุลาคม 2569</span>
      <a href="files/notice.pdf">ประกาศรับสมัครครูผู้ช่วย2569.pdf</a>
    </body></html>
    """
    parsed = parse_notice_page(html, "https://www.surat2.go.th/2021/news/detail/7834")
    assert parsed["special"] is False
    assert parsed["announced_at"] == "2026-10-12"
    assert parsed["application_start"] == "2026-10-19"
    assert parsed["application_end"] == "2026-10-25"
    truncated = parse_notice_page(
        """
        <html><head><meta name="description" content="รับสมัครครูผู้ช่วย ปี พ.ศ. 2569 ระหว่างวันที่ 19 - 25 ตุลาคม ..."></head>
        <body><h1>รับสมัครครูผู้ช่วย</h1><span>12 ตุลาคม 2569</span></body></html>
        """,
        "https://www.surat2.go.th/2021/news/detail/7834",
    )
    assert truncated["application_start"] == "2026-10-19"
    assert truncated["application_end"] == "2026-10-25"
    assert parsed["documents"][0]["url"].endswith("notice.pdf")
    links = find_notice_links(
        "<a href='/news/1'>รับสมัครสอบแข่งขันครูผู้ช่วย ปี 2569</a><a href='/news/2'>ประกาศผู้ชนะการเสนอราคา</a>",
        "https://www.surat2.go.th/2021/frontpage",
    )
    assert len(links) == 1
    assert links[0]["kind"] == "recruitment_notice"


def test_placeholder_zero_is_not_an_applicant_count():
    text = "ภาษาอังกฤษ\nจำนวนสมัคร 0\nอัตราการแข่งขัน -"
    result = interpret_notice_text(text)
    assert result["applicants"] is None
    assert result["openings"] is None


def test_past_call_link_requires_a_stated_year():
    html = """
    <a href="/new">ประกาศรับสมัครครูผู้ช่วย 2569</a>
    <a href="/old">ประกาศเรียกบรรจุครูผู้ช่วย 2567</a>
    <a href="/plain">เรียกบรรจุครูผู้ช่วย</a>
    """
    links = find_past_call_links(html, "https://area.example.go.th/")
    assert len(links) == 1
    assert links[0]["exam_year"] == 2567
    assert links[0]["url"].endswith("/old")
