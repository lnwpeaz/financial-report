"""Bundled PP-OCRv5 Thai inference. No API, model download or image upload at runtime."""
from functools import lru_cache
from pathlib import Path
from difflib import SequenceMatcher
import re
import sys

from .extract import (AMOUNT_LABEL, FEE_LABEL, NOTE_LABELS, amount_values,
                      clean_party_line, compact, detect_site, is_account, is_bank,
                      is_party_line, label_pattern, parse_amount, parse_date, parse_slip)

MODEL_FILES = ("ch_PP-OCRv5_det_mobile.onnx", "th_PP-OCRv5_rec_mobile.onnx")
# Names supplied as examples by the user; these only match memo/caption regions.
# Unknown site codes remain literal codes, never mapped to a guessed full name.
DEFAULT_SITES = [{"name": "สยาม", "aliases": ["SI"]}, {"name": "นนท์", "aliases": []},
                 {"name": "หัวหิน", "aliases": []}]
NOTE_START = re.compile(r"^\s*(?:" + "|".join(label_pattern(s) for s in NOTE_LABELS) + r")[\s:：\-]*(.*)$", re.I)


def model_dir():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / ("neural-ocr" if getattr(sys, "frozen", False) else "vendor/neural-ocr")


@lru_cache(maxsize=1)
def engine():
    from rapidocr import RapidOCR, OCRVersion, ModelType
    directory = model_dir()
    for name in MODEL_FILES:
        if not (directory / name).is_file():
            raise FileNotFoundError(f"ไม่พบโมเดลภาษาไทย {name}")
    return RapidOCR(params={
        "Global.model_root_dir": str(directory), "Global.use_cls": False,
        "Global.log_level": "error", "Global.text_score": .35,
        "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
        "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
        "Rec.lang_type": "th", "Det.model_path": str(directory / MODEL_FILES[0]),
        "Rec.model_path": str(directory / MODEL_FILES[1]),
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    })


def bounds(record):
    box = record["box"]
    return min(p[0] for p in box), min(p[1] for p in box), max(p[0] for p in box), max(p[1] for p in box)


def clean_records(records):
    """Remove duplicate boxes around individual diacritics, not uncertain words."""
    result = []
    for row in records:
        a,b,c,d = bounds(row)
        area = max(1, (c-a)*(d-b))
        duplicate = False
        for other in records:
            if row is other:
                continue
            x,y,z,w = bounds(other)
            overlap = max(0,min(c,z)-max(a,x))*max(0,min(d,w)-max(b,y))
            if (z-x)*(w-y) > area*2 and overlap/area > .65:
                duplicate = True
                break
        if not duplicate:
            result.append(row)
    return sorted(result, key=lambda r: (sum(p[1] for p in r["box"])/4, bounds(r)[0]))


def read_records(image):
    import numpy as np
    # RapidOCR expects BGR for numpy input; retain the original image colours.
    result = engine()(np.array(image.convert("RGB"))[:, :, ::-1], use_det=True, use_cls=False, use_rec=True)
    records = clean_records([{"text": t, "score": float(s), "box": b.tolist()}
                             for t,s,b in zip(result.txts or [], result.scores or [], result.boxes if result.boxes is not None else [])])
    return refine_records(image, records)


def refine_records(image, records):
    """Re-read name/memo lines with extra room for Thai marks above the letters."""
    from collections import Counter
    import numpy as np
    bank = next((r for r in records if is_bank(r['text'])),None)
    for row in records:
        x,y,z,w = bounds(row)
        is_memo = bool(NOTE_START.match(row['text']))
        near_party = bool(bank and x >= bounds(bank)[0]-image.width*.04 and bounds(bank)[1]-image.width*.20 < y < .66*image.height
                          and is_party_line(row['text']) and not re.search(r"^[\dXx*\s.-]+$",row['text']))
        expense = bool(re.search(r"เบิก|^ค่า(?:น้ำ|ไฟ|วัสดุ|แรง)",row['text']))
        if not (is_memo or near_party or expense):
            continue
        options=[]
        for margin in [0,.10,.22]:
            pad=(w-y)*margin
            with image.crop((max(0,int(x-3)),max(0,int(y-pad)),min(image.width,int(z+3)),min(image.height,int(w+pad)))) as crop:
                result=engine()(np.array(crop.convert('RGB'))[:,:,::-1],use_det=False,use_cls=False,use_rec=True)
            if result.txts:
                value=result.txts[0]
                if is_memo:
                    value=re.sub(r'^บันทึกช่วยจ(?:ำา|า)', 'บันทึกช่วยจำ',value)
                options.append((value,float(result.scores[0])))
        if not options:
            continue
        counts=Counter(compact(value) for value,_ in options)
        # Prefer agreement. If all differ, retain the candidate nearest the other
        # readings; a warning records the uncertainty instead of deleting a field.
        def rank(item):
            value,score=item
            normalized=compact(value)
            agreement=counts[normalized]
            similarity=sum(SequenceMatcher(None,normalized,compact(v)).ratio() for v,_ in options)
            return agreement,similarity,score
        chosen=max(options,key=rank)
        row['initial_text']=row['text']
        row['text'],row['score']=chosen
        row['alternatives']=[value for value,_ in options]
        row['uncertain']=len(counts)>1 and counts[compact(chosen[0])]<2
    return records


def unique_names(rows):
    values = []
    for row in rows:
        text = clean_party_line(row["text"])
        if not is_party_line(text):
            continue
        normalized = re.sub(r"[^\w]", "", text).lower()
        def duplicate_name(value):
            other = re.sub(r"[^\w]", "", value).lower()
            company = ("บริษัท", "บจก", "บมจ", "บจ", "หจก", "หจ")
            return normalized == other or (normalized.startswith(company) and other.startswith(company)
                    and SequenceMatcher(None, normalized, other).ratio() > .9)
        match = next((i for i,(value,score) in enumerate(values) if duplicate_name(value)), None)
        if match is None:
            values.append((text, row["score"]))
        elif row["score"] > values[match][1]:
            values[match] = (text,row["score"])
    return "\n".join(value for value,score in values)


def parse_records(records, width, height, rules=None):
    records = clean_records(records)
    text = "\n".join(r["text"] for r in records)
    slip = parse_slip(text, rules)
    slip.warnings = [w for w in slip.warnings if w.startswith("พบสกุลเงินอื่น")]
    first_bank = next((r for r in records if is_bank(r["text"])), None)
    is_kplus = bool(first_bank and "กสิกรไทย" in compact(first_bank["text"]) and
                    any(re.search(r"K\s*\+|K\s*BIZ", r["text"], re.I) for r in records))
    identity_end = 0
    relevant = []
    if is_kplus:
        bx,by,_,bb = bounds(first_bank)
        account = next((r for r in records if bounds(r)[1] > bb-5 and is_account(r["text"])), None)
        if account:
            bottom = bounds(account)[3]
            boundary = next((r for r in records if bounds(r)[1]>bottom and
                             (AMOUNT_LABEL.search(r["text"]) or re.search(r"(?:เลข|เลย|เลช|เลฃ).*รายการ", compact(r["text"])))), None)
            identity_end = bounds(boundary)[1] if boundary else height*.75
            candidates = [r for r in records if bounds(r)[0] >= bx-width*.04 and
                          bounds(r)[3] < by+5 and bounds(r)[1] > max(0,by-width*.16)
                          and is_party_line(r["text"]) and compact(r["text"]) not in {"จาก","ไปยัง"}]
            if candidates:
                slip.sender = unique_names(candidates)
                relevant.extend(candidates)
            recipient = []
            for r in records:
                left,top,right,bot = bounds(r)
                if bot <= bottom+5 or top >= identity_end or left < bx-width*.05:
                    continue
                value = r["text"]
                if is_bank(value) or is_account(value) or re.search(r"พร้อมเพย์|prompt|\bpay\b",value,re.I) or len(re.findall(r"\d",value))>=5:
                    break
                if compact(value) not in {"ไปยัง","จาก","ไป"} and is_party_line(value):
                    recipient.append(r)
            if recipient:
                slip.recipient = unique_names(recipient)
                relevant.extend(recipient)

    amount_rows = []
    for i,row in enumerate(records):
        if not AMOUNT_LABEL.search(row["text"]) or FEE_LABEL.search(row["text"]):
            continue
        for candidate in records[i:i+8]:
            if candidate is not row and (FEE_LABEL.search(candidate["text"]) or NOTE_START.match(candidate["text"])):
                break
            value = candidate["text"]
            normalized = re.sub(r"(?<=\d)[:;](?=\d{2}(?:\s*(?:บาท|THB)|$))", ".", value)
            amounts = amount_values(normalized, allow_integer=True)
            if amounts:
                amount_rows.append((candidate, amounts[0]))
                if normalized != value:
                    slip.warnings.append("ยอดเงิน: OCR อ่านจุดทศนิยมคล้ายเครื่องหมายอื่น ใส่ค่าที่อ่านได้ให้ก่อน")
                break
    if amount_rows:
        best, value = max(amount_rows, key=lambda p:p[0]["score"])
        slip.amount = value
        relevant.append(best)
        if len(set(value for _,value in amount_rows))>1:
            slip.warnings.append("ยอดเงิน: พบหลายยอด ใส่ยอดจากช่องจำนวนเงินให้ก่อน")

    note_rows = []
    for i,row in enumerate(records):
        if match := NOTE_START.match(row["text"]):
            note_rows.append({**row,"text":match[1]})
            _,_,_,bottom = bounds(row)
            for following in records[i+1:]:
                if bounds(following)[1] < bottom-3:
                    continue
                if re.search(r"สแกน|เลขที่|QR|ค่าธรรมเนียม",following["text"],re.I):
                    break
                note_rows.append(following)
            break
    # Extra captions are outside sender/payee blocks. Keep expense/site captions
    # in the memo even if no printed memo label was used.
    captions = []
    for row in records:
        value = row["text"].strip()
        if is_kplus and bounds(row)[1] > identity_end and not NOTE_START.match(value):
            if re.fullmatch(r"[ก-๙A-Za-z][ก-๙A-Za-z ._-]{1,35}",value) and not re.search(r"สแกน|บาท|รายการ|จำนวน|ธรรมเนียม|กสิกร|ตรวจสอบ|K\+|QR",value,re.I):
                if row not in note_rows and row["score"] >= .6:
                    captions.append(row)
    notes = [r["text"] for r in note_rows if r["text"].strip()]
    for r in captions:
        if r["text"] not in notes:
            notes.append(r["text"])
    if notes:
        slip.note = "\n".join(notes)
        relevant.extend(note_rows)

    # Non-bank expense captions: keep the visible note and labelled withdrawal
    # amount. Never infer a missing person's identity from other slips.
    if not is_kplus:
        expenses = [r for r in records if re.search(r"เบิก|^ภาษี|^ค่า(?:แรง|วัสดุ|น้ำ|ไฟ)",r["text"])]
        if expenses and not slip.note:
            slip.note = "\n".join(r["text"] for r in expenses)
        if not slip.amount:
            for row in expenses:
                if match := re.search(r"เบิก\s*([0-9,]+(?:\.\d{2})?)(?!\d)",row["text"]):
                    from .model import money
                    try:
                        slip.amount = str(money(match[1]))
                        slip.warnings.append("ยอดเงินจากข้อความเบิกประกอบภาพ ไม่ใช่ช่องยอดโอนธนาคาร")
                    except ValueError:
                        pass
        if re.search(r"ใบเสนอราคา",text):
            # A table's "จำนวน" is quantity, not a transfer amount.
            for i,row in enumerate(records):
                if "รวมสุทธิ" in compact(row['text']):
                    candidate = parse_amount([r['text'] for r in records[i:i+3]])
                    if candidate:
                        slip.amount = candidate
                        break
            slip.warnings.append("ภาพเป็นใบเสนอราคา ยอดเป็นราคารวม ไม่ใช่หลักฐานชำระเงิน")
        if "Thank you for using" in text and "@" in text and not slip.recipient:
            merchant = re.search(r"@\s*(.+?)(?=\d{1,2}/\d{1,2}/\d{4})",text,re.S)
            if merchant:
                slip.recipient = re.sub(r"\s+"," ",merchant[1]).strip()
                slip.warnings.append("ภาพเป็นข้อความแจ้งใช้บัตร ระบุร้านค้าเป็นผู้รับ")

    all_rules = list(DEFAULT_SITES if rules is None else rules)
    # Thai memo text often omits the space in "งานนนท์". Only split a known
    # site after an explicit work/site word, never inside a person's name.
    site_note = slip.note
    for rule in all_rules:
        for alias in [rule['name'],*rule.get('aliases',[])]:
            site_note = re.sub(r"(งาน|ไซต์|ไซท์)(?="+re.escape(alias)+r"(?:\s|$))",r"\1 ",site_note,flags=re.I)
    site_text = text + ("\nบันทึกช่วยจำ: " + site_note if site_note else "")
    slip.site, site_warnings = detect_site(site_text, all_rules)
    if not slip.site and slip.note:
        codes = re.findall(r"(?:^|\n)\s*([A-Z]{2,5})(?=\s+(?i:roof|driver|room|pre\s*function|site|งาน|ห้อง|หลังคา))|งาน\s*([A-Z]{2,5})(?![A-Za-z])",slip.note)
        codes = list(dict.fromkeys(a or b for a,b in codes if (a or b) not in {'VAT','PVC','THB','USD','TOA','AIS','PTT','SCB','KTB','LED'}))
        if len(codes)==1:
            slip.site = codes[0]
            slip.warnings.append("ไซต์เป็นคำย่อจากบันทึก: " + codes[0])
    if not slip.site:
        slip.warnings.extend(w for w in site_warnings if not w.startswith("ไม่พบชื่อไซต์"))
    if not slip.reference:
        for i,row in enumerate(records):
            if re.search(r"(?:เลข|เลย|เลช).*รายการ",compact(row['text'])):
                for r in records[i:i+3]:
                    if match := re.search(r"(?<!\w)[A-Za-z0-9]{10,40}(?!\w)",r['text']):
                        slip.reference = match[0]
                        break
    for label,key in [("วันที่","date"),("ผู้รับ","recipient"),("ผู้ส่ง","sender"),("จำนวนเงิน","amount")]:
        if not getattr(slip,key):
            slip.warnings.append(f"ยังอ่าน{label}ไม่ได้ หรือไม่มีระบุในภาพ")
    if any(r['score'] < .8 for r in relevant):
        slip.warnings.append("ข้อความบางช่องอ่านไม่ชัด ใส่ผลที่อ่านได้ให้ก่อน")
    if any(r.get('uncertain') for r in relevant):
        slip.warnings.append("ชื่อหรือบันทึกอ่านต่างกันระหว่างรอบ ใส่ข้อความที่ใกล้เคียงให้ก่อน")
    scores=[r['score'] for r in records]
    slip.confidence = round(100*sum(scores)/len(scores),1) if scores else 0
    slip.raw_text = "[PP-OCRv5 Thai]\n" + text
    return slip


def recognize(image, rules=None):
    return parse_records(read_records(image), image.width, image.height, rules)
