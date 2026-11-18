import pytest
from slipreport.extract import parse_amount, parse_slip


@pytest.mark.parametrize("lines, expected", [
    (["จำนวน:", "3,470.00 บ าท", "ค่ าธรรม เนียม:", "0.00 บาท"], "3470.00"),
    (["จำนวน:", "[m]", "QR noise", "another line", "450.00 บ า ท", "ค่าธรรมเนียม:", "10.00 บาท"], "450.00"),
    (["จำนวน:", "ไม่ชัด", "ค่าธรรมเนียม:", "10.00 บาท"], ""),
    (["จำนวน:", "123;450.00 บาท"], ""),
    (["จำนวน:", "1.000.00 บาท"], ""),
    (["0040.00 บาท"], ""),
    (["จำนวน:", "3 , 470 . 00 บาท", "ค่าธรรมเนียม:", "0.00 บาท"], "3470.00"),
    (["จำนวน:", "3,470.00 บาท", "3,570.00 บาท", "ค่าธรรมเนียม:", "0.00 บาท"], ""),
])
def test_kplus_amounts_and_never_select_a_fee_or_partial_number(lines, expected):
    assert parse_amount(lines) == expected


def test_kplus_parties_without_sender_recipient_labels():
    slip = parse_slip("""โอนเงินสำเร็จ
2 มิ.ย. 69 13:06 น.
5 65) นาย กิตติ ใจดี
ธ.กสิกรไทย
XXX-X-X1234-X
นาย สมชาย ทดสอบ
OC) ธ.ไทยพาณิชย์
%%%-%-%5678-%
เลขที่รายการ:
TEST202606020001
จำนวน:
3,470.00 บ าท
ค่าธรรมเนียม:
0.00 บาท
บันทึกช่วยจำ: ซื้อสี""")
    assert slip.sender == "นาย กิตติ ใจดี"
    assert slip.recipient == "นาย สมชาย ทดสอบ"
    assert slip.amount == "3470.00"
    assert slip.date == "2026-06-02"


def test_kplus_merchant_names_can_have_multiple_lines():
    slip = parse_slip("""ชำระเงินสำเร็จ
2 มิ.ย. 69
นาย กิตติ ใจดี
ธ.กสิกรไทย
xxx-x-x1234-x
ร้านวัสดุ - สาขาทดสอบ
บริษัท วัสดุทดสอบ จำกัด
202606020000001
เลขที่รายการ:
TEST202606020001
จำนวน: 808.00 บาท""")
    assert slip.sender == "นาย กิตติ ใจดี"
    assert slip.recipient == "ร้านวัสดุ - สาขาทดสอบ\nบริษัท วัสดุทดสอบ จำกัด"


def test_kbiz_multiline_names_stop_before_bank():
    slip = parse_slip("""จาก
บริษัท ก่อสร้างทดสอบ
จำกัด
ธนาคารกสิกรไทย
XXX-X-X1234-X
ไปยัง
ห้างหุ้นส่วนจำกัด วัสดุ
สาขาทดสอบ
ธนาคารโทยพาณิชย์
XXX-X-X5678-X
จำนวนเงิน
157,317.47 บาท""")
    assert slip.sender == "บริษัท ก่อสร้างทดสอบ\nจำกัด"
    assert slip.recipient == "ห้างหุ้นส่วนจำกัด วัสดุ\nสาขาทดสอบ"


def test_unknown_layout_does_not_reverse_parties_when_recipient_bank_is_kbank():
    slip = parse_slip("""นาย กิตติ
ธ.กรุงไทย
xxx-x-x1234-x
นาย สมชาย
ธ.กสิกรไทย
xxx-x-x5678-x
เลขที่รายการ:
TEST202606020001
จำนวน: 100.00 บาท""")
    assert slip.sender == ""
    assert slip.recipient == ""


@pytest.mark.parametrize("values, expected", [
    (("118.00", "9118.00", "5118.00"), ""),
    (("3470.00", "3470.00", ""), "3470.00"),
    (("", "1000.00", ""), ""),
    (("122.00", "1712200.00", "122.00"), "122.00"),
    (("", "808.00", "808.00"), "808.00"),
])
def test_conflicting_amount_readings_require_review(values, expected):
    from slipreport.ocr import consensus_amount
    assert consensus_amount(*values) == expected
