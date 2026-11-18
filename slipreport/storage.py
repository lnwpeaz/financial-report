from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter
from .model import HEADERS, SITE_RULE_VERSION, Slip, money
from .ocr import file_hash

SHEET = "รายการสลิป"
META = "_imports"
REVIEW = "ตรวจสอบ"
META_HEADERS = ["sha256", "pixels_sha256", "source", "reference", "imported_utc", "ocr_text"]


class FileLock:
    """OS-backed lock is released on process exit, including after a crash."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RuntimeError("โฟลเดอร์นี้กำลังใช้งานอยู่ กรุณาปิดโปรแกรมอีกหน้าต่างก่อน") from exc
        self.stream = stream
        return self

    def release(self):
        if self.stream:
            self.stream.close()
            self.stream = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def initialize(root):
    root = Path(root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / ".slipreport").mkdir(exist_ok=True)
    sites = root / "sites.json"
    defaults_marker = root / ".slipreport" / "sites-v13-initialized"
    if not sites.exists() or (not defaults_marker.exists() and sites.read_text(encoding="utf-8-sig").strip()=="[]"):
        from .neural import DEFAULT_SITES
        sites.write_text(json.dumps(DEFAULT_SITES,ensure_ascii=False,indent=2), encoding="utf-8")
    defaults_marker.touch(exist_ok=True)
    with db(root) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS drafts (hash TEXT PRIMARY KEY, payload TEXT NOT NULL)")


@contextmanager
def db(root):
    conn = sqlite3.connect(Path(root) / ".slipreport" / "cache.sqlite3", timeout=10)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def save_draft(root, slip):
    with db(root) as conn:
        conn.execute("INSERT OR REPLACE INTO drafts(hash, payload) VALUES (?, ?)", (slip.file_hash, json.dumps(slip.to_dict(), ensure_ascii=False)))


def backup_drafts(root):
    folder = Path(root) / ".slipreport" / "cache-backups"
    folder.mkdir(exist_ok=True)
    path = folder / (datetime.now().strftime("cache-%Y%m%d-%H%M%S-%f") + ".sqlite3")
    with db(root) as source:
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            target.close()
    return path


def all_drafts(root):
    with db(root) as conn:
        result = []
        for row in conn.execute("SELECT payload FROM drafts ORDER BY rowid"):
            payload = json.loads(row[0])
            payload.setdefault("site_rule_version", 0)
            payload.setdefault("ocr_version", 0)
            result.append(Slip(**payload))
        return result


def read_sites(root):
    data = json.loads((Path(root) / "sites.json").read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("sites.json ต้องเป็นรายการชื่อไซต์")
    names = set()
    for rule in data:
        if not isinstance(rule, dict) or not isinstance(rule.get("name"), str) or not rule["name"].strip():
            raise ValueError("แต่ละไซต์ต้องมี name เป็นข้อความ")
        if not isinstance(rule.get("aliases", []), list) or any(not isinstance(a, str) for a in rule.get("aliases", [])):
            raise ValueError("aliases ต้องเป็นรายการข้อความ")
        if rule["name"] in names:
            raise ValueError("ชื่อไซต์ซ้ำ: " + rule["name"])
        names.add(rule["name"])
    return data


def save_sites(root, rules):
    path = Path(root) / "sites.json"
    temp = path.with_name("sites." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def check_workbook(book):
    if SHEET not in book.sheetnames:
        raise ValueError("report.xlsx ไม่มีชีต 'รายการสลิป' กรุณาใช้ไฟล์ที่โปรแกรมสร้าง หรือเลือกโฟลเดอร์งานใหม่")
    sheet = book[SHEET]
    if [sheet.cell(1, i).value for i in range(1, 7)] != HEADERS:
        raise ValueError("หัวคอลัมน์ใน report.xlsx ถูกเปลี่ยน กรุณาคืนหัวคอลัมน์เดิมก่อนบันทึก")
    if META not in book.sheetnames:
        raise ValueError("ไม่พบประวัติกันซ้ำใน report.xlsx กรุณาคืนไฟล์จาก backups (อย่าลบชีต _imports)")
    if [book[META].cell(1, i).value for i in range(1, 7)] != META_HEADERS:
        raise ValueError("ประวัติกันซ้ำใน report.xlsx ไม่ถูกต้อง กรุณาคืนไฟล์จาก backups")


def imported_from_book(book):
    check_workbook(book)
    hashes, pixels, references = set(), set(), set()
    for values in book[META].iter_rows(min_row=2, max_col=4, values_only=True):
        h, p, _, ref = values
        if h:
            hashes.add(h)
        if p:
            pixels.add(p)
        if ref:
            references.add(str(ref))
    return hashes, pixels, references


def imported(root):
    path = Path(root) / "report.xlsx"
    if not path.exists():
        return set(), set(), set()
    book = load_workbook(path, read_only=True)
    try:
        return imported_from_book(book)
    finally:
        book.close()


def pending(root):
    hashes, pixels, _ = imported(root)
    result = [s for s in all_drafts(root) if s.file_hash not in hashes and s.pixel_hash not in pixels]
    for slip in result:
        if slip.site_rule_version < SITE_RULE_VERSION:
            # Old cache has no manual/automatic provenance. Preserve edits but
            # require review again so the old broad matcher cannot silently export.
            slip.reviewed = False
            slip.site_rule_version = SITE_RULE_VERSION
            slip.warnings.insert(0, "รายการค้างจากรุ่นเก่า กรุณาตรวจชื่อไซต์ใหม่ เพราะกฎเดิมอาจจับจากชื่อคน")
            save_draft(root, slip)
    return result


def set_text(cell, value):
    # Explicit strings protect against formula injection from OCR or manual input.
    cell.value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))[:32767]
    cell.data_type = "s"


def new_book():
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(HEADERS)
    sheet.freeze_panes = "A2"
    sheet.sheet_view.showGridLines = False
    for i, width in enumerate([16, 28, 32, 32, 20, 60], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
        cell = sheet.cell(1, i)
        cell.font = Font(name="Tahoma", bold=True, color="FFFFFF", size=11)
        cell.fill = PatternFill("solid", fgColor="163F45")
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 30
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:1"
    metadata = book.create_sheet(META)
    metadata.append(META_HEADERS)
    metadata.sheet_state = "veryHidden"
    return book


def review_sheet(book):
    if REVIEW in book.sheetnames:
        sheet = book[REVIEW]
        if sheet.cell(1,1).value != "แถวในรายการสลิป":
            raise ValueError("มีชีตชื่อ ตรวจสอบ ที่ไม่ใช่ของโปรแกรม กรุณาเปลี่ยนชื่อชีตนั้นก่อน")
        return sheet
    sheet = book.create_sheet(REVIEW)
    sheet.append(["แถวในรายการสลิป", "ไฟล์สลิป", "สถานะ", "สิ่งที่ควรตรวจ"])
    sheet.freeze_panes = "A2"
    for i,width in enumerate([22,65,22,95],1):
        sheet.column_dimensions[get_column_letter(i)].width = width
        sheet.cell(1,i).font = Font(name="Tahoma",bold=True,color="FFFFFF")
        sheet.cell(1,i).fill = PatternFill("solid",fgColor="163F45")
    return sheet


def append_report(root, slips, *, allow_unreviewed=False):
    """Workbook + dedup ledger commit together. A failed save never marks imported."""
    root = Path(root)
    slips = list(slips)
    for slip in slips:
        if not slip.reviewed and not allow_unreviewed:
            raise ValueError("กรุณาตรวจและยืนยันรายการก่อนบันทึก Excel")
        slip.validate_partial() if allow_unreviewed else slip.validate()
    if not slips:
        return 0, 0
    with FileLock(root / ".slipreport" / "export.lock"):
        destination = root / "report.xlsx"
        if (root / "~$report.xlsx").exists():
            raise PermissionError("กรุณาปิด report.xlsx ใน Excel ก่อนบันทึก")
        original_hash = file_hash(destination) if destination.exists() else None
        book = load_workbook(destination) if destination.exists() else new_book()
        temporary = root / (".report-" + uuid.uuid4().hex + ".xlsx")
        try:
            hashes, pixels, _ = imported_from_book(book)
            sheet, metadata = book[SHEET], book[META]
            checks = review_sheet(book) if allow_unreviewed else None
            added, skipped = 0, 0
            for slip in slips:
                if slip.file_hash in hashes or slip.pixel_hash in pixels:
                    skipped += 1
                    continue
                row = sheet.max_row + 1
                sheet.cell(row, 1, date.fromisoformat(slip.date) if slip.date else None).number_format = "yyyy-mm-dd"
                for col, value in [(2, slip.site), (3, slip.recipient), (4, slip.sender), (6, slip.note)]:
                    set_text(sheet.cell(row, col), value)
                sheet.cell(row, 5, float(money(slip.amount)) if slip.amount else None).number_format = '#,##0.00'
                for cell in sheet[row][:6]:
                    cell.font = Font(name="Tahoma", size=11, color="18383D")
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if row % 2 == 0:
                        cell.fill = PatternFill("solid", fgColor="F0F7F5")
                if checks is not None:
                    notices = list(slip.warnings)
                    for key,label,col in [("date","วันที่",1),("recipient","ผู้รับ",3),("sender","ผู้ส่ง",4),("amount","ยอดเงิน",5)]:
                        if not getattr(slip,key):
                            sheet.cell(row,col).fill = PatternFill("solid",fgColor="FFF0C2")
                            notices.append("ช่อง" + label + "ว่าง")
                    check_row = checks.max_row+1
                    checks.cell(check_row,1,row)
                    checks.cell(check_row,1).hyperlink = f"#'{SHEET}'!A{row}"
                    for col,value in [(2,slip.source),(3,"ตรวจแล้ว" if slip.reviewed else "ยังไม่ได้ตรวจ"),(4,"\n".join(dict.fromkeys(notices)))]:
                        set_text(checks.cell(check_row,col),value)
                        checks.cell(check_row,col).alignment = Alignment(vertical="top",wrap_text=True)
                    if Path(slip.source).parts and Path(slip.source).parts[0]=="data" and ".." not in Path(slip.source).parts:
                        checks.cell(check_row,2).hyperlink = slip.source
                    sheet.cell(row,5).comment = Comment("ไฟล์: " + slip.source + "\n" + ("ตรวจแล้ว" if slip.reviewed else "ผลอ่านอัตโนมัติ ยังไม่ได้ตรวจ") + ("\n"+"\n".join(dict.fromkeys(notices)) if notices else ""),"SlipToExcel")
                # Leave height unset so Excel can expand wrapped notes.
                meta_row = metadata.max_row + 1
                for col, value in enumerate([slip.file_hash, slip.pixel_hash, slip.source, slip.reference, datetime.now(timezone.utc).isoformat(), slip.raw_text], 1):
                    set_text(metadata.cell(meta_row, col), value)
                hashes.add(slip.file_hash)
                pixels.add(slip.pixel_hash)
                added += 1
            if not added:
                return 0, skipped
            sheet.auto_filter.ref = f"A1:F{sheet.max_row}"
            if checks is not None:
                checks.auto_filter.ref = f"A1:D{checks.max_row}"
            book.save(temporary)
            # Flush the completed zip before touching the previous workbook.
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            current_hash = file_hash(destination) if destination.exists() else None
            if current_hash != original_hash:
                raise RuntimeError("Excel ถูกแก้ไขระหว่างบันทึก กรุณาลองใหม่ ข้อมูลเดิมยังอยู่ครบ")
            if destination.exists():
                backup_dir = root / "backups"
                backup_dir.mkdir(exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                shutil.copy2(destination, backup_dir / f"report-{stamp}.xlsx")
            os.replace(temporary, destination)
            return added, skipped
        finally:
            book.close()
            temporary.unlink(missing_ok=True)
