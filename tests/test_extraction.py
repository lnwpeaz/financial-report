import pytest
from slipreport.extract import detect_site, parse_amount, parse_date, parse_slip
from slipreport.model import money


@pytest.mark.parametrize("text,expected", [
    ("4 พ.ย. 2569 13:02", "2026-11-04"), ("๔ พฤศจิกายน ๒๕๖๙", "2026-11-04"),
    ("4 พ. ย. 69", "2026-11-04"), ("04/11/2569", "2026-11-04"),
    ("04-11-2026", "2026-11-04"), ("2026-11-04", "2026-11-04"),
    ("04 Nov 2026", "2026-11-04"), ("31/02/2026", ""),
    ("ไม่มีวันที่", ""), ("04/11/69", "2026-11-04"),
])
def test_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("lines,expected", [
    (["จำนวนเงิน", "1,250.50 บาท", "ค่าธรรมเนียม 10.00 บาท"], "1250.50"),
    (["Amount: 12,345.67 THB", "Fee: 0.00 THB"], "12345.67"),
    (["จ ำ น ว น เ งิ น", "123.00", "ค่าธรรมเนียม", "0.00"], "123.00"),
    (["ค่าธรรมเนียม 10.00 บาท"], ""),
    (["จำนวนเงิน 100 บาท"], "100.00"),
    (["เลขบัญชี 1234567890", "04/11/2026"], ""),
    (["1,250.50", "ค่าธรรมเนียม", "0.00"], "1250.50"),
])
def test_amount(lines, expected):
    assert parse_amount(lines) == expected


def test_full_thai_slip():
    text = """โอนเงินสำเร็จ
4 พ.ย. 2569 10:30
จาก
นาย สมชาย ใจดี
xxx-123-4
ไปยัง
บริษัท วัสดุดี จำกัด
จำนวนเงิน 12,345.67 บาท
ค่าธรรมเนียม 0.00 บาท
บันทึกความจำ: ซื้อวัสดุ LP
เลขที่รายการ: 20261104000001234"""
    slip = parse_slip(text, [{"name": "บ้านลาดพร้าว", "aliases": ["LP"]}])
    assert slip.sender == "นาย สมชาย ใจดี"
    assert slip.recipient == "บริษัท วัสดุดี จำกัด"
    assert slip.amount == "12345.67"
    assert slip.site == "บ้านลาดพร้าว"
    assert slip.note == "ซื้อวัสดุ LP"
    assert slip.reference == "20261104000001234"


def test_site_ambiguous_and_token_boundaries():
    rules = [{"name": "ลาดพร้าว", "aliases": ["LP"]}, {"name": "บางนา", "aliases": ["BN"]}]
    assert detect_site("LP และ BN", rules)[0] == ""
    assert detect_site("HELP", rules)[0] == ""
    assert detect_site("ค่าแรง ลาด พร้าว", rules)[0] == "ลาดพร้าว"
    assert detect_site("ข้อความลอย ๆ LP", rules)[0] == ""
    assert detect_site("โครงการ: บ้านคุณเอ", [])[0] == "บ้านคุณเอ"


SITE_RULES = [{"name": "สยาม", "aliases": ["Siam"]}, {"name": "นนท์", "aliases": ["นนทบุรี"]}]


@pytest.mark.parametrize("text, expected", [
    ("ผู้รับ: คุณสยาม\nบันทึกความจำ: ค่าวัสดุ นนท์", "นนท์"),
    ("ผู้รับ\nนาย อานนท์ ใจดี\nบันทึกความจำ: ค่าวัสดุ สยาม", "สยาม"),
    ("ผู้รับ: นายอานนท์", ""),
    ("ผู้รับ: นนท์", ""),
    ("บันทึกความจำ: จ่ายคุณสยาม", ""),
    ("บันทึกความจำ: จ่าย คุณ สยาม", ""),
    ("บันทึกความจำ: ค่าแรง อานนท์", ""),
    ("บันทึกความจำ: ค่าแรง คุณนนท์ ไซต์สยาม", "สยาม"),
    ("บันทึกความจำ: ค่าวัสดุ นนท์\nไซต์: สยาม", "สยาม"),
    ("บันทึกความจำ: ค่าวัสดุ สยาม และ นนท์", ""),
    ("บันทึกความจำ: ไซต์สยาม\nหมายเหตุ: ไซต์นนท์", ""),
    ("บันทึกความจำ: ค่าวัสดุ น น ท์", "นนท์"),
    ("บันทึกความจำ: ค่าวัสดุ SIAM", "สยาม"),
    ("บันทึกความจำ: ค่าวัสดุ นนทบุรี", "นนท์"),
    ("สยาม\nโอนเงินสำเร็จ", ""),
    ("ผู้รับ\nไซต์ สยาม", ""),
    ("ผู้รับ: บริษัท โครงการ สยาม จำกัด\nบันทึกความจำ: ค่าวัสดุ นนท์", "นนท์"),
    ("ผู้รับ: Site Siam Ltd\nMemo: Materials นนท์", "นนท์"),
    ("บันทึกความจำ: ค่าวัสดุ นนท", ""),
    ("บันทึกความจำ: จ่าย บริษัท โครงการสยาม", ""),
    ("บันทึกความจำ: จ่าย คุณ โครงการ สยาม", ""),
])
def test_site_context_and_person_name_collision(text, expected):
    assert detect_site(text, SITE_RULES)[0] == expected


def test_no_fabrication():
    slip = parse_slip("ภาพไม่ชัด")
    assert not any([slip.date, slip.amount, slip.sender, slip.recipient, slip.site])
    assert slip.warnings


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "1.234", "=1+1", "", "9999999999999"])
def test_invalid_amount(value):
    with pytest.raises(ValueError):
        money(value)
