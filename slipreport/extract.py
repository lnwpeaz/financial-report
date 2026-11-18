"""Conservative Thai/English parsing. Missing information stays blank for review."""
import re
from datetime import date
from .model import Slip, money

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
MONTHS = [
    ("มกราคม", "มค", "jan", "january"), ("กุมภาพันธ์", "กพ", "feb", "february"),
    ("มีนาคม", "มีค", "mar", "march"), ("เมษายน", "เมย", "apr", "april"),
    ("พฤษภาคม", "พค", "may"), ("มิถุนายน", "มิย", "jun", "june"),
    ("กรกฎาคม", "กค", "jul", "july"), ("สิงหาคม", "สค", "aug", "august"),
    ("กันยายน", "กย", "sep", "sept", "september"), ("ตุลาคม", "ตค", "oct", "october"),
    ("พฤศจิกายน", "พย", "nov", "november"), ("ธันวาคม", "ธค", "dec", "december"),
]


def compact(value):
    return re.sub(r"[\s.\u200b]+", "", value).casefold()


def label_pattern(label):
    # Thai OCR sometimes inserts spaces inside words, including combining marks.
    if any("\u0e00" <= c <= "\u0e7f" for c in label):
        return r"\s*".join(re.escape(c) for c in label)
    return re.escape(label) + r"\b"


def year_value(value, thai_context=False):
    year = int(value)
    if year >= 2400:
        return year - 543
    if year < 100:
        return 2500 + year - 543 if thai_context or year >= 40 else 2000 + year
    return year


def make_date(day, month, year, thai_context=False):
    try:
        result = date(year_value(year, thai_context), int(month), int(day))
        if not 1900 <= result.year <= 2199:
            return ""
        return result.isoformat()
    except ValueError:
        return ""


def parse_date(text):
    normalized = text.translate(THAI_DIGITS)
    # OCR can join the year and time: "25 มิ.ย.6911:32" / "22/06/202612:47".
    normalized = re.sub(r"(\d{2,4})(?=\d{2}:\d{2})", r"\1 ", normalized)
    for y, m, d in re.findall(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", normalized):
        if result := make_date(d, m, y):
            return result
    for d, m, y in re.findall(r"(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})(?!\d)", normalized):
        if result := make_date(d, m, y):
            return result
    for line in normalized.splitlines():
        line = re.sub(r"[.\u200b]", "", line).casefold()
        for month, names in enumerate(MONTHS, 1):
            for name in sorted(names, key=len, reverse=True):
                month_pattern = r"\s*".join(re.escape(c) for c in name)
                if match := re.search(r"(?<!\d)(\d{1,2})\s*" + month_pattern + r"\s*(?:พ\s*ศ|ค\s*ศ)?\s*(\d{4}|\d{2})(?!\d)", line):
                    if result := make_date(match[1], month, match[2], any("\u0e00" <= c <= "\u0e7f" for c in name)):
                        return result
    return ""


SECTION_LABELS = ["จำนวนเงิน", "จำนวน", "ค่าธรรมเนียม", "บันทึก", "หมายเหตุ", "ข้อความ", "เลขที่", "รหัส", "ชื่อผู้รับ", "ชื่อผู้ส่ง", "ชื่อผู้โอน", "ผู้รับ", "ผู้โอน", "ผู้ส่ง", "จาก", "ไปยัง", "วันที่", "รับเงิน", "โอนไป", "โอนจาก", "amount", "fee", "memo", "note", "remark", "reference", "sender", "recipient", "from", "to"]
NOTE_LABELS = ["บันทึกช่วยจำ", "บันทึกความจำ", "บันทึก", "หมายเหตุ", "ข้อความ", "memo", "note", "remark"]
SITE_LABELS = ["ไซต์งาน", "ไซท์งาน", "ไซต์", "ไซท์", "โครงการ", "หน้างาน", "สถานที่", "site", "location"]
IDENTITY_LABELS = ["ชื่อผู้รับ", "ชื่อผู้ส่ง", "ชื่อผู้โอน", "ผู้รับ", "ผู้โอน", "ผู้ส่ง", "โอนจาก", "จาก", "โอนไป", "ไปยัง", "ไป", "sender", "recipient", "from", "to"]


def is_section(line):
    return any(re.match(r"^\s*" + label_pattern(s), line, re.I) for s in SECTION_LABELS)


def field_value(lines, labels):
    for label in labels:
        pattern = re.compile(r"^\s*" + label_pattern(label) + r"\s*[:：\-]?\s*(.*)$", re.I)
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if not match:
                continue
            value = match[1].strip()
            if value:
                return value
            parts = []
            for following in lines[i + 1:i + 7]:
                if is_section(following) or is_bank(following) or is_account(following):
                    break
                if len(re.findall(r"\d", following)) >= 5:
                    break
                if is_party_line(following):
                    parts.append(clean_party_line(following))
            if parts:
                return "\n".join(parts)
    return ""


AMOUNT_LABEL = re.compile("|".join(label_pattern(s) for s in ["จำนวนเงิน", "จำนวน", "ยอดโอน", "ยอดชำระ", "amount"]), re.I)
FEE_LABEL = re.compile("|".join(label_pattern(s) for s in ["ค่าธรรมเนียม", "คาธรรมเนียม", "คาธรรมเนยม", "fee"]), re.I)
CURRENCY = re.compile(label_pattern("บาท") + r"|\bTHB\b|฿", re.I)
DECIMAL_NUMBER = r"(?<![\w.,;:])(?!0\d)(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d.,;:])"


def amount_values(line, allow_integer=False):
    # Normalize spaces around printed punctuation; never repair missing digits.
    line = re.sub(r"(?<=\d)[ \t]*([,.])[ \t]*(?=\d)", r"\1", line)
    result = []
    for match in re.finditer(DECIMAL_NUMBER, line):
        try:
            result.append(str(money(match[0])))
        except ValueError:
            pass
    if not result and allow_integer:
        if match := re.search(r"(?<![\w.,;:])(\d{1,3}(?:,\d{3})+|\d+)[ \t]*(?:" + CURRENCY.pattern + ")", line, re.I):
            try:
                result.append(str(money(match[1])))
            except ValueError:
                pass
    return result


def parse_amount(lines):
    labelled = []
    for i, line in enumerate(lines):
        if AMOUNT_LABEL.search(line) and not FEE_LABEL.search(line):
            for following in lines[i:i + 10]:
                if FEE_LABEL.search(following) or any(re.search(label_pattern(s), following, re.I) for s in NOTE_LABELS):
                    break
                labelled.extend(amount_values(following, allow_integer=True))
    labelled = list(dict.fromkeys(labelled))
    if labelled:
        return labelled[0] if len(labelled) == 1 else ""
    candidates, standalone = [], []
    in_fee = False
    for line in lines:
        if FEE_LABEL.search(line):
            in_fee = True
        if in_fee:
            continue
        if CURRENCY.search(line):
            candidates.extend(amount_values(line))
        elif re.fullmatch(DECIMAL_NUMBER, line):
            standalone.extend(amount_values(line))
    candidates = list(dict.fromkeys(candidates or standalone))
    return candidates[0] if len(candidates) == 1 else ""


def is_account(line):
    value = compact(line)
    return len(re.findall(r"[xX*%×]", value)) >= 2 and len(re.findall(r"\d", value)) >= 2


def is_bank(line):
    value = compact(line)
    return "ธนาคาร" in value or bool(re.search(r"ธ(?:กสิกรไทย|ไทยพาณิชย์|กรุงไทย|กรุงเทพ|กรุงศรี|ทหารไทย|ออมสิน|ธนชาต|เกียรตินาคิน|อาคารสงเคราะห์)", value))


def clean_party_line(line):
    # Remove edge decoration, preserving the name as printed (including truncation).
    line = line.strip(" |'\"_—-<>:;~")
    for title in ["นางสาว", "นาง", "นาย"]:
        if match := re.search(label_pattern(title), line):
            if len(re.findall(r"[A-Za-z\u0e01-\u0e2e]", line[:match.start()])) < 3:
                return title + " " + line[match.end():].lstrip()
    return line


def is_party_line(line):
    line = clean_party_line(line)
    if compact(line) in {"จำกัด", "มหาชน", "(มหาชน)"}:
        return True
    if not line or is_bank(line) or is_account(line) or is_section(line):
        return False
    if re.search(r"สำเร็จ|สําเร็จ|สแกน|บาท|THB|K\s*BIZ", line, re.I) or parse_date(line):
        return False
    letters = re.findall(r"[A-Za-z\u0e01-\u0e2e\u0e40-\u0e45]", line)
    return len(letters) >= 3 and not re.fullmatch(r"[\d\s.,:/\-]+", line)


def parse_kplus_parties(lines):
    """K+ names are ordered above bank/account blocks, without From/To labels.

    Only use the layout when the sender's Kasikorn bank + masked account and
    the transaction section are present. Do not infer parties from arbitrary text.
    """
    first_bank = next((i for i, line in enumerate(lines) if is_bank(line)), None)
    if first_bank is None or "กสิกรไทย" not in compact(lines[first_bank]):
        return "", ""
    first_account = next((i for i in range(first_bank + 1, min(len(lines), first_bank + 5)) if is_account(lines[i])), None)
    boundary = next((i for i, line in enumerate(lines) if re.search(label_pattern("เลขที่รายการ") + "|" + AMOUNT_LABEL.pattern, line, re.I)), None)
    if first_account is None or boundary is None or boundary <= first_account:
        return "", ""
    send_candidates = [clean_party_line(line) for line in lines[max(0, first_bank - 3):first_bank] if is_party_line(line)]
    sender = send_candidates[-1] if send_candidates else ""
    recipient_lines = []
    for line in lines[first_account + 1:boundary]:
        if is_bank(line) or is_account(line):
            break
        if is_party_line(line):
            recipient_lines.append(clean_party_line(line))
    recipient = "\n".join(recipient_lines[:3])
    return sender, recipient


def note_parts(lines):
    parts = []
    for i, line in enumerate(lines):
        for label in NOTE_LABELS:
            if match := re.match(r"^\s*" + label_pattern(label) + r"(?=$|[\s:：\-])\s*[:：\-]?\s*(.*)$", line, re.I):
                chunks = [match[1]] if match[1] else []
                for following in lines[i + 1:i + 7]:
                    if is_section(following) or re.search(r"QR|สแกน|ตรวจสอบรายการ", following, re.I):
                        break
                    chunks.append(following)
                parts.extend(chunks)
                break
    return parts


def parse_note(lines):
    return "\n".join(note_parts(lines)).strip()


def detect_site(text, rules):
    """Use site declarations/memos, never a bare occurrence in a person's name.

    This is a conservative rule-based matcher, not Thai named-entity recognition.
    Unlabelled floating words and ambiguous text remain for human review.
    """
    text = re.sub("ํ\\s*า", "ำ", text.replace("\u200b", ""))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    word = r"A-Za-z0-9_\u0e00-\u0e7f"

    def matching_names(value):
        matches = []
        for rule in rules:
            for alias in [rule["name"], *rule.get("aliases", [])]:
                alias = alias.strip()
                if not alias:
                    continue
                # Allow OCR-inserted spaces in Thai, but require actual word edges.
                if any("\u0e00" <= c <= "\u0e7f" for c in alias):
                    pattern = r"[ \t]*".join(re.escape(c) for c in alias if not c.isspace())
                else:
                    pattern = re.escape(alias).replace(r"\ ", r"[ \t]+")
                if re.search(rf"(?<![{word}])(?:{pattern})(?![{word}])", value, re.I):
                    matches.append(rule["name"])
                    break
        return list(dict.fromkeys(matches))

    def starts_with(line, labels):
        return any(re.match(r"^" + label_pattern(label), line, re.I) for label in labels)

    site_pattern = re.compile(rf"(?<![{word}])(?:" + "|".join(label_pattern(label) for label in SITE_LABELS) + r")[ \t]*[:：\-]?[ \t]*(.+)$", re.I)
    person_pattern = re.compile("|".join(label_pattern(label) for label in ["คุณ", "นาย", "นางสาว", "นาง", "น.ส.", "บริษัท", "หจก.", "Mr", "Mrs", "Ms", "Miss", "Dr"]), re.I)
    notes = note_parts(lines)
    captions, declarations = [], []
    in_identity = False
    for line in lines:
        if starts_with(line, IDENTITY_LABELS):
            in_identity = True
            continue
        if is_section(line):
            in_identity = False
            continue
        if in_identity:
            continue
        if starts_with(line, SITE_LABELS):
            declarations.append(line)
        elif not person_pattern.search(line) and re.match(r"^(?:" + "|".join(label_pattern(label) for label in ["ค่าแรง", "ค่าวัสดุ", "ค่าอุปกรณ์", "ค่าของ", "ค่าช่าง"]) + ")", line):
            captions.append(line)

    explicit = []
    for line in [*notes, *declarations, *captions]:
        if match := site_pattern.search(line):
            # "บริษัท โครงการสยาม" is a payee, not a site declaration.
            prefix = line[:match.start()].rstrip()
            if any(re.search(label_pattern(title) + r"\s*$", prefix, re.I) for title in ["คุณ", "นาย", "นาง", "นางสาว", "บริษัท", "หจก.", "Mr", "Mrs", "Ms"]):
                continue
            value = match[1].strip(" :：-|,")
            names = matching_names(value)
            # Also expose unknown explicitly named sites, but never fabricate one.
            explicit.extend(names or [value])
    explicit = list(dict.fromkeys(explicit))
    if len(explicit) == 1:
        return explicit[0], ["ชื่อไซต์จากข้อความระบุไซต์ กรุณาตรวจสอบ"]
    if len(explicit) > 1:
        return "", ["ข้อความระบุหลายไซต์: " + ", ".join(explicit)]

    matches, person_ambiguous = [], False
    for line in [*notes, *captions]:
        names = matching_names(line)
        if person_pattern.search(line):
            person_ambiguous = person_ambiguous or bool(names)
            continue
        matches.extend(names)
    matches = list(dict.fromkeys(matches))
    if len(matches) == 1 and not person_ambiguous:
        return matches[0], []
    if len(matches) > 1:
        return "", ["พบหลายไซต์ในบันทึก: " + ", ".join(matches)]
    if person_ambiguous:
        return "", ["ชื่อในบันทึกอาจเป็นชื่อคน กรุณาเลือกไซต์เอง"]
    if matching_names(text):
        return "", ["พบคำคล้ายชื่อไซต์นอกบริบทที่ชัดเจน กรุณาตรวจจากภาพ (ไม่นำชื่อผู้รับ/ผู้ส่งมาเลือกไซต์)"]
    return "", ["ไม่พบชื่อไซต์ที่ชัดเจน เว้นว่างได้หากไม่มีระบุในสลิป"]


def parse_slip(text, rules=None):
    text = text.translate(THAI_DIGITS).replace("\u200b", "")
    text = re.sub("ํ\\s*า", "ำ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip()]
    slip = Slip(raw_text=text)
    slip.date = parse_date(text)
    slip.amount = parse_amount(lines)
    slip.sender = field_value(lines, ["ชื่อผู้โอน", "ชื่อผู้ส่ง", "ผู้โอน", "ผู้ส่ง", "โอนจาก", "จาก", "sender", "from"])
    slip.recipient = field_value(lines, ["ชื่อผู้รับ", "ผู้รับเงิน", "ผู้รับ", "โอนไปยัง", "โอนไป", "ไปยัง", "ไป", "recipient", "to"])
    layout_sender, layout_recipient = parse_kplus_parties(lines)
    slip.sender = slip.sender or layout_sender
    slip.recipient = slip.recipient or layout_recipient
    slip.note = parse_note(lines)
    slip.site, slip.warnings = detect_site(text, rules or [])
    for line in lines:
        if re.search(label_pattern("เลขที่รายการ") + "|" + label_pattern("เลขอ้างอิง") + "|" + label_pattern("รหัสอ้างอิง") + r"|\bref(?:erence)?\b", line, re.I):
            if match := re.search(r"(?<!\w)[A-Za-z0-9]{10,40}(?!\w)", line):
                slip.reference = match[0]
                break
    for label, value in [("วันที่", slip.date), ("จำนวนเงิน", slip.amount), ("ผู้รับ", slip.recipient), ("ผู้ส่ง", slip.sender)]:
        if not value:
            slip.warnings.append(f"อ่าน{label}ไม่ได้")
    if re.search(r"\b(?:USD|EUR|JPY|GBP)\b|ดอลลาร์|ยูโร", text, re.I):
        slip.warnings.append("พบสกุลเงินอื่น โปรแกรมนี้บันทึกจำนวนเงินเป็นบาท กรุณาตรวจสอบ")
    return slip
