import hashlib
import os
from pathlib import Path
import sys
import re
from statistics import median

from PIL import Image, ImageOps, ImageChops
import pytesseract
from .extract import AMOUNT_LABEL, FEE_LABEL, clean_party_line, compact, is_bank, is_account, is_party_line, parse_amount, parse_date, parse_slip
from .model import money

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def tesseract_path():
    root = resource_root()
    candidates = [root / "tesseract" / "tesseract.exe", root / "vendor" / "tesseract" / "tesseract.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("ไม่พบ OCR ภาษาไทย กรุณาใช้ชุดโปรแกรมที่มีไฟล์ OCR ครบ")


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_image(path):
    with Image.open(path) as original:
        if getattr(original, "n_frames", 1) > 1:
            raise ValueError("รูปมีหลายหน้า กรุณาแยกเป็น 1 รูปต่อ 1 สลิป")
        original.load()
        im = ImageOps.exif_transpose(original).convert("RGBA")
        canvas = Image.new("RGBA", im.size, "white")
        return Image.alpha_composite(canvas, im).convert("RGB")


def pixel_hash(image):
    digest = hashlib.sha256()
    digest.update(f"{image.width}x{image.height}:{image.mode}".encode("ascii"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def render_line(output, indices):
    """Thai is tokenized into glyphs; preserve visual word gaps, not TSV token gaps."""
    line_height = median(output["height"][i] for i in indices)
    parts = []
    previous = None
    for i in indices:
        token = output["text"][i].strip()
        if previous is not None:
            gap = output["left"][i] - output["left"][previous] - output["width"][previous]
            last = output["text"][previous].strip()[-1]
            both_thai = "\u0e00" <= last <= "\u0e7f" and "\u0e00" <= token[0] <= "\u0e7f"
            if not both_thai or gap > max(5, line_height * .28):
                parts.append(" ")
        parts.append(token)
        previous = i
    return "".join(parts)


def normalized_text(text):
    return re.sub("ํ\\s*า", "ำ", text).replace("\u200b", "")


def read_lines(im, psm=6):
    output = pytesseract.image_to_data(im, lang="tha+eng", config=f"--psm {psm} --oem 1", output_type=pytesseract.Output.DICT, timeout=90)
    lines, scores = {}, []
    for i, token in enumerate(output["text"]):
        if not token.strip():
            continue
        key = (output["block_num"][i], output["par_num"][i], output["line_num"][i])
        lines.setdefault(key, []).append(i)
        score = float(output["conf"][i])
        if score >= 0:
            scores.append(score)
    records = []
    for indices in lines.values():
        records.append({
            "text": normalized_text(render_line(output, indices)),
            "left": min(output["left"][i] for i in indices),
            "top": min(output["top"][i] for i in indices),
            "right": max(output["left"][i] + output["width"][i] for i in indices),
            "bottom": max(output["top"][i] + output["height"][i] for i in indices),
            "tokens": [{"left": output["left"][i], "top": output["top"][i], "right": output["left"][i] + output["width"][i], "bottom": output["top"][i] + output["height"][i]} for i in indices],
        })
    return records, scores


def read_region(gray, box, *, psm=6, lang="tha+eng", threshold=180, scale=1):
    crop = gray.crop(tuple(int(x) for x in box))
    if scale != 1:
        enlarged = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
        crop.close()
        crop = enlarged
    binary = crop.point(lambda value: 255 if value > threshold else 0)
    crop.close()
    try:
        return normalized_text(pytesseract.image_to_string(binary, lang=lang, config=f"--psm {psm} --oem 1", timeout=45)).strip()
    finally:
        binary.close()


def consensus_amount(*values):
    """Require agreement from at least two distinct OCR layouts for K+ amounts."""
    choices = {value: values.count(value) for value in values if value}
    agreed = [value for value, count in choices.items() if count >= 2]
    return agreed[0] if len(agreed) == 1 else ""


def reference_text(image):
    # Preserve the original full-page OCR scale as a separate comparison. Two
    # threshold variants of the same cropped glyph are not independent evidence.
    factor = min(2.0, 1800 / image.width, 3600 / image.height)
    luminance = ImageOps.autocontrast(ImageOps.grayscale(image).resize((round(image.width * factor), round(image.height * factor)), Image.Resampling.LANCZOS))
    try:
        rows, _ = read_lines(luminance, psm=11)
        return "\n".join(row["text"] for row in rows)
    finally:
        luminance.close()


def read_kplus_number(image, lang="eng", threshold=130):
    """Only called after checking the standard K+ bank/account layout anchors."""
    width = image.width
    crop = image.crop((int(.04 * width), int(1.04 * width), int(.50 * width), int(1.115 * width)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.BICUBIC).point(lambda value: 255 if value > threshold else 0)
    try:
        text = pytesseract.image_to_string(crop, lang=lang, config="--psm 7 --oem 1 -c tessedit_char_whitelist=0123456789.,", timeout=45).strip()
    finally:
        crop.close()
    value = ""
    if re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}", text):
        try:
            value = str(money(text))
        except ValueError:
            pass
    return text, value


def recognize_legacy(image, rules=None):
    exe = tesseract_path()
    pytesseract.pytesseract.tesseract_cmd = str(exe)
    os.environ.setdefault("OMP_THREAD_LIMIT", "2")
    os.environ["TESSDATA_PREFIX"] = str(exe.parent / "tessdata")
    # Suppress light watermark/background detail while retaining dark printed text.
    factor = min(2.0, 2200 / max(image.width, 1), 4000 / max(image.height, 1))
    # The lightest channel removes much of K+'s cyan watermark without uploading
    # the image. Dark grey printed text remains dark in all three channels.
    red, green, blue = image.split()
    source_gray = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    red.close()
    green.close()
    blue.close()
    resized = source_gray.resize((max(1, round(source_gray.width * factor)), max(1, round(source_gray.height * factor))), Image.Resampling.LANCZOS)
    gray = resized
    binary = gray.point(lambda value: 255 if value > 140 else 0)
    try:
        records, scores = read_lines(binary)
    finally:
        binary.close()
    text = "\n".join(row["text"] for row in records)
    slip = parse_slip(text, rules)
    extra, notices = [], []
    bank_rows = [row for row in records if is_bank(row["text"])]
    is_kplus = bool(bank_rows and "กสิกรไทย" in compact(bank_rows[0]["text"]))
    account_rows = [row for row in records if is_account(row["text"])]
    standard_kplus = bool(is_kplus and account_rows and 1.23 <= gray.height / gray.width <= 1.65 and .28 <= bank_rows[0]["top"] / gray.width <= .39 and .36 <= account_rows[0]["top"] / gray.width <= .48)
    amount_checked = False
    fallback = None
    try:
        if is_kplus or not slip.date:
            # Read the header without the green vertical bar and bank logo.
            header = read_region(gray, (.065 * gray.width, 0, .80 * gray.width, .20 * gray.height))
            header_date = parse_date(header)
            extra.append("วันที่ / หัวสลิป:\n" + header)
            if header_date:
                if slip.date and header_date != slip.date:
                    notices.append("วันที่จากการอ่านสองรอบไม่ตรงกัน กรุณาตรวจจากรูป")
                    slip.date = ""
                else:
                    slip.date = header_date
        if is_kplus:
            # Re-read each selected name line without the nearby bank logo/arrow.
            for field in ["sender", "recipient"]:
                current = getattr(slip, field)
                if not current:
                    continue
                name_rows = [row for row in records if any(compact(clean_party_line(row["text"])) == compact(part) for part in current.splitlines())]
                replacements = []
                for row in name_rows:
                    tokens = [token for token in row["tokens"] if token["left"] >= .22 * gray.width]
                    if not tokens:
                        continue
                    left, top = min(t["left"] for t in tokens), min(t["top"] for t in tokens)
                    right, bottom = max(t["right"] for t in tokens), max(t["bottom"] for t in tokens)
                    height = bottom - top
                    region = (max(.22 * gray.width, left - 10), max(0, top - height * .30), min(gray.width, right + 18), min(gray.height, bottom + height * .30))
                    lang = "tha" if not re.search(r"[A-Za-z]", row["text"]) else "tha+eng"
                    name = clean_party_line(read_region(source_gray, tuple(value / factor for value in region), psm=7, lang=lang, scale=3))
                    if is_party_line(name):
                        replacements.append(name)
                if len(replacements) == len(name_rows) and replacements:
                    setattr(slip, field, "\n".join(replacements))
                    extra.append(("ผู้ส่ง" if field == "sender" else "ผู้รับ") + ": " + getattr(slip, field))
            if standard_kplus:
                # These ratios describe this K+ layout, validated by bank/account
                # anchors above. They are never applied to arbitrary bank slips.
                sender_text = read_region(source_gray, (.25 * source_gray.width, .24 * source_gray.width, .99 * source_gray.width, .325 * source_gray.width), psm=7, lang="tha+eng", scale=3)
                recipient_text = read_region(source_gray, (.25 * source_gray.width, .58 * source_gray.width, .99 * source_gray.width, .79 * source_gray.width), psm=6, lang="tha+eng", scale=3)
                sender_candidate = clean_party_line(sender_text)
                if is_party_line(sender_candidate) and "\n" not in sender_candidate:
                    slip.sender = sender_candidate
                parts = []
                for line in recipient_text.splitlines():
                    if is_bank(line) or is_account(line) or len(re.findall(r"\d", line)) >= 5:
                        break
                    if is_party_line(line):
                        parts.append(clean_party_line(line))
                if parts:
                    slip.recipient = "\n".join(parts)
                extra.extend(["ผู้ส่ง / อ่านตามรูปแบบ K+:\n" + sender_text, "ผู้รับ / อ่านตามรูปแบบ K+:\n" + recipient_text])
                number_text, number = read_kplus_number(source_gray)
                extra.append("จำนวนเงิน / เฉพาะตัวเลข:\n" + number_text)
                whole_amount = slip.amount
                reference_amount = ""
                if not number or whole_amount != number:
                    fallback_text = reference_text(image)
                    fallback = parse_slip(fallback_text, rules)
                    reference_amount = fallback.amount
                    extra.append("อ่านภาพรวมเทียบยอด:\n" + fallback_text)
                slip.amount = consensus_amount(whole_amount, number, reference_amount)
                amount_checked = True
                if not slip.amount:
                    notices.append("ยอดเงินจากการอ่านหลายวิธีไม่สอดคล้องกัน กรุณาตรวจจากรูป")
            # Isolate the amount from fees and the QR code on nonstandard layouts.
            amount_row = next((row for row in records if AMOUNT_LABEL.search(row["text"]) and not FEE_LABEL.search(row["text"])), None)
            if amount_row and not standard_kplus:
                fee_row = next((row for row in records if row["top"] > amount_row["top"] and FEE_LABEL.search(row["text"])), None)
                lower = fee_row["top"] - 3 if fee_row else min(gray.height, amount_row["bottom"] + gray.width * .14)
                if lower > amount_row["bottom"]:
                    amount_text = read_region(gray, (.025 * gray.width, amount_row["bottom"], .64 * gray.width, lower))
                    amount = parse_amount(["จำนวน:", *amount_text.splitlines()])
                    extra.append("จำนวนเงิน / อ่านแยกจากค่าธรรมเนียม:\n" + amount_text)
                    if amount and slip.amount and amount != slip.amount:
                        slip.amount = ""
                        notices.append("ยอดเงินจากการอ่านสองรอบไม่ตรงกัน กรุณาตรวจจากรูป")
                    elif amount:
                        slip.amount = amount
        # A second segmentation mode only fills fields that are still missing.
        if any(not getattr(slip, key) for key in ["date", "amount", "sender", "recipient"]):
            if fallback is None:
                fallback_text = reference_text(image)
                fallback = parse_slip(fallback_text, rules)
                extra.append("อ่านเพิ่มเติม:\n" + fallback_text)
            for key in ["date", "amount", "sender", "recipient", "note"]:
                conflict = (key == "date" and any(n.startswith("วันที่จาก") for n in notices)) or (key == "amount" and (amount_checked or any(n.startswith("ยอดเงินจาก") for n in notices)))
                if not getattr(slip, key) and not conflict:
                    setattr(slip, key, getattr(fallback, key))
    finally:
        gray.close()
        source_gray.close()
    slip.raw_text = text + ("\n\n--- OCR เฉพาะบริเวณ ---\n" + "\n".join(extra) if extra else "")
    slip.warnings = [w for w in slip.warnings if w not in ["อ่านวันที่ไม่ได้", "อ่านจำนวนเงินไม่ได้", "อ่านผู้รับไม่ได้", "อ่านผู้ส่งไม่ได้"]]
    slip.warnings.extend(notices)
    if is_kplus and (slip.sender or slip.recipient):
        slip.warnings.append("ชื่อแยกตามตำแหน่งสลิป K+ กรุณาตรวจตัวสะกดและวรรณยุกต์กับรูป")
    for label, key in [("วันที่", "date"), ("จำนวนเงิน", "amount"), ("ผู้รับ", "recipient"), ("ผู้ส่ง", "sender")]:
        if not getattr(slip, key):
            slip.warnings.append(f"อ่าน{label}ไม่ได้")
    slip.confidence = round(sum(scores) / len(scores), 1) if scores else 0.0
    if slip.confidence < 65:
        slip.warnings.append("ข้อความบางส่วนไม่ชัดหรือเป็นลายมือ กรุณาตรวจจากภาพ")
    if not text.strip():
        slip.warnings.append("ไม่พบข้อความในภาพ สามารถกรอกข้อมูลด้วยตนเอง")
    return slip


def recognize(image, rules=None):
    """Prefer the Thai neural model; retain best-effort fields instead of blanking conflicts."""
    from .neural import recognize as neural_recognize
    try:
        slip = neural_recognize(image, rules)
    except Exception as exc:
        slip = recognize_legacy(image, rules)
        slip.warnings.insert(0, f"ตัวอ่านภาษาไทยใหม่ทำงานไม่ได้ ใช้ตัวอ่านสำรอง: {exc}")
        return slip
    # Only spend a fallback pass on a missing date/amount in a bank-like image.
    # Missing names in a cash photo must not be invented from nearby captions.
    bank_like = any(word in compact(slip.raw_text) for word in ["กสิกรไทย", "โอนเงิน", "โอนสำเร็จ", "ชำระเงิน", "transfer"])
    if bank_like and (not slip.date or not slip.amount):
        fallback = recognize_legacy(image, rules)
        for key in ["date", "amount", "sender", "recipient", "note"]:
            if not getattr(slip, key) and getattr(fallback, key):
                setattr(slip, key, getattr(fallback, key))
                slip.warnings.append("ใช้ข้อความจากตัวอ่านสำรองในช่อง " + key)
        slip.raw_text += "\n\n[OCR สำรอง]\n" + fallback.raw_text
        for label,key in [("วันที่","date"),("ผู้รับ","recipient"),("ผู้ส่ง","sender"),("จำนวนเงิน","amount")]:
            if getattr(slip,key):
                slip.warnings=[w for w in slip.warnings if w!=f"ยังอ่าน{label}ไม่ได้ หรือไม่มีระบุในภาพ"]
    return slip
