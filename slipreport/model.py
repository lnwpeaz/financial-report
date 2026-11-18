from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import re

HEADERS = ["date", "site", "ผู้รับ", "ผู้ส่ง", "จำนวนเงิน", "หมายเหตุ / บันทึกความจำ"]
SITE_RULE_VERSION = 2
OCR_VERSION = 3


def money(value: str) -> Decimal:
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
        raise ValueError("จำนวนเงินต้องเป็นตัวเลขบวก เช่น 1,250.50")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("จำนวนเงินไม่ถูกต้อง") from exc
    if not result.is_finite() or result <= 0 or result >= Decimal("1000000000000"):
        raise ValueError("จำนวนเงินต้องมากกว่า 0 และน้อยกว่า 1 ล้านล้านบาท")
    return result.quantize(Decimal("0.01"))


@dataclass
class Slip:
    file_hash: str = ""
    pixel_hash: str = ""
    source: str = ""
    date: str = ""
    site: str = ""
    recipient: str = ""
    sender: str = ""
    amount: str = ""
    note: str = ""
    reference: str = ""
    raw_text: str = ""
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    reviewed: bool = False
    site_rule_version: int = SITE_RULE_VERSION
    manual_fields: list[str] = field(default_factory=list)
    ocr_version: int = OCR_VERSION

    def to_dict(self):
        return asdict(self)

    def validate(self):
        try:
            parsed_date = date.fromisoformat(self.date)
            if not 1900 <= parsed_date.year <= 2199:
                raise ValueError("ใช้ปี ค.ศ.")
        except (ValueError, TypeError) as exc:
            raise ValueError("วันที่ต้องเป็น ค.ศ. รูปแบบ YYYY-MM-DD เช่น 2026-11-04") from exc
        # A slip without a site still belongs in the report; leave that cell blank.
        for label, value in [("ผู้รับ", self.recipient), ("ผู้ส่ง", self.sender)]:
            if not value.strip():
                raise ValueError(f"กรุณาระบุ{label} (ถ้าไม่มีในสลิป ให้ใส่ 'ไม่ระบุ')")
        money(self.amount)
        if not self.file_hash or not self.pixel_hash:
            raise ValueError("ไม่มีรหัสภาพต้นฉบับ กรุณาสแกนใหม่")

    def validate_partial(self):
        """Missing fields are allowed; supplied dates and amounts still keep their types."""
        if self.date:
            try:
                parsed = date.fromisoformat(self.date)
                if not 1900 <= parsed.year <= 2199:
                    raise ValueError()
            except (TypeError, ValueError) as exc:
                raise ValueError("วันที่ต้องเป็น ค.ศ. YYYY-MM-DD หรือเว้นว่าง") from exc
        if self.amount:
            money(self.amount)
        if not self.file_hash or not self.pixel_hash:
            raise ValueError("ไม่มีรหัสภาพต้นฉบับ กรุณาสแกนใหม่")
