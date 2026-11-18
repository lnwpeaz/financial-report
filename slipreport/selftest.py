"""A synthetic, offline check that also runs from the packaged executable."""
from pathlib import Path
import tempfile
import traceback

from PIL import Image, ImageDraw, ImageFont
from openpyxl import load_workbook
from .ocr import file_hash, load_image, pixel_hash, recognize, tesseract_path
from .storage import SHEET, append_report, initialize


def synthetic_image(path):
    image = Image.new("RGB", (1200, 1500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("tahoma.ttf", 40)
    title = ImageFont.truetype("tahomabd.ttf", 50)
    draw.text((65, 65), "TEST SLIP / ข้อมูลทดสอบ", font=title, fill="#193D43")
    lines = ["Transfer successful", "Date: 2026-11-04", "From: Somchai Test", "To: Supplier Demo", "Amount: 1,250.50 THB", "Memo: Materials for Site ALPHA", "จำนวนเงิน 1,250.50 บาท", "บันทึกความจำ: ค่าวัสดุ ไซต์ ALPHA", "Reference: TEST202611040001"]
    for i, line in enumerate(lines):
        draw.text((70, 220 + i * 115), line, font=font, fill="#142C30")
    image.save(path)
    image.close()


def run():
    result = {"ok": False, "checks": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="slipreport-test-") as temp:
            root = Path(temp)
            initialize(root)
            path = root / "data" / "synthetic.png"
            synthetic_image(path)
            image = load_image(path)
            slip = recognize(image, [{"name": "ALPHA", "aliases": []}])
            slip.file_hash, slip.pixel_hash, slip.source = file_hash(path), pixel_hash(image), "data/synthetic.png"
            image.close()
            result["checks"]["ocr_text"] = bool(slip.raw_text)
            result["checks"]["neural_engine"] = slip.raw_text.startswith("[PP-OCRv5 Thai]")
            result["checks"]["ocr_date"] = slip.date == "2026-11-04"
            result["checks"]["ocr_amount"] = slip.amount == "1250.50"
            # Check complete expected Thai memo words, not a single currency glyph
            # that mixed Thai/English OCR sometimes renders as Latin letters.
            thai_text = slip.raw_text.replace(" ", "")
            result["checks"]["ocr_thai"] = all(word in thai_text for word in ["บันทึกความจำ", "ค่าวัสดุ"])
            result["checks"]["ocr_names"] = "Somchai" in slip.sender and "Supplier" in slip.recipient
            result["checks"]["ocr_site"] = slip.site == "ALPHA"
            slip.reviewed = True
            added, skipped = append_report(root, [slip])
            result["checks"]["first_append"] = (added, skipped) == (1, 0)
            result["checks"]["duplicate_skipped"] = append_report(root, [slip]) == (0, 1)
            book = load_workbook(root / "report.xlsx")
            result["checks"]["excel_amount_numeric"] = book[SHEET]["E2"].value == 1250.50
            result["checks"]["excel_rows"] = book[SHEET].max_row == 2
            book.close()
            result["ocr_engine"] = "PP-OCRv5 Thai / ONNX Runtime (bundled, offline)"
            result["extracted"] = {k: v for k, v in slip.to_dict().items() if k not in {"file_hash", "pixel_hash", "raw_text"}}
            result["raw_text"] = slip.raw_text
            result["ok"] = all(result["checks"].values())
    except Exception:
        result["error"] = traceback.format_exc()
    return result
