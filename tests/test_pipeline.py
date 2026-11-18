from dataclasses import replace
from pathlib import Path
import shutil
from unittest.mock import patch

from PIL import Image, PngImagePlugin
from openpyxl import load_workbook
import pytest

from slipreport.model import Slip
from slipreport.service import scan
from slipreport.storage import FileLock, META, SHEET, all_drafts, append_report, imported, initialize, pending, save_draft


@pytest.fixture
def workspace(tmp_path):
    initialize(tmp_path)
    return tmp_path


def sample(**kwargs):
    return Slip(**dict(dict(file_hash="hash-a", pixel_hash="pixels-a", source="data/a.png", date="2026-11-04", site="ลาดพร้าว", recipient="ร้านวัสดุ", sender="สมชาย", amount="1250.50", note="ซื้อวัสดุ", reviewed=True), **kwargs))


def test_append_preserves_edits_and_backup(workspace):
    first = sample()
    assert append_report(workspace, [first]) == (1, 0)
    report = workspace / "report.xlsx"
    book = load_workbook(report)
    book[SHEET]["F2"] = "ผู้ใช้แก้หมายเหตุ"
    book.create_sheet("สรุปของฉัน")["A1"] = "=SUM('รายการสลิป'!E2:E50)"
    book.save(report)
    book.close()
    assert append_report(workspace, [first, sample(file_hash="hash-b", pixel_hash="pixels-b", amount="200.00")]) == (1, 1)
    book = load_workbook(report)
    assert book[SHEET].max_row == 3
    assert book[SHEET]["F2"].value == "ผู้ใช้แก้หมายเหตุ"
    assert book[SHEET]["E3"].value == 200
    assert book[SHEET]["A2"].value.year == 2026
    assert book["สรุปของฉัน"]["A1"].data_type == "f"
    assert book[META].sheet_state == "veryHidden"
    book.close()
    assert len(list((workspace / "backups").glob("*.xlsx"))) == 1


def test_workbook_ledger_is_authority_after_restart(workspace):
    slip = sample()
    save_draft(workspace, slip)
    append_report(workspace, [slip])
    assert pending(workspace) == []
    # Lose the cache after a crash or move: the workbook still prevents duplicate imports.
    (workspace / ".slipreport" / "cache.sqlite3").unlink()
    initialize(workspace)
    assert append_report(workspace, [slip]) == (0, 1)


def test_same_pixels_different_file_skipped(workspace):
    assert append_report(workspace, [sample(), sample(file_hash="other")]) == (1, 1)


def test_no_site_still_imports(workspace):
    assert append_report(workspace, [sample(site="", note="")]) == (1, 0)
    book = load_workbook(workspace / "report.xlsx")
    assert book[SHEET]["B2"].value is None
    assert book[SHEET]["E2"].value == 1250.5
    assert book[SHEET].max_row == 2
    book.close()


def test_old_pending_drafts_require_site_review_without_losing_edits(workspace):
    import json
    from slipreport.storage import db
    payload = sample(note="แก้เองไว้แล้ว").to_dict()
    del payload["site_rule_version"]
    with db(workspace) as conn:
        conn.execute("INSERT INTO drafts(hash,payload) VALUES (?,?)", (payload["file_hash"], json.dumps(payload)))
    draft = pending(workspace)[0]
    assert not draft.reviewed
    assert draft.site == payload["site"]
    assert draft.note == "แก้เองไว้แล้ว"
    assert "รุ่นเก่า" in draft.warnings[0]
    draft.reviewed = True
    save_draft(workspace, draft)
    assert pending(workspace)[0].reviewed


def test_excel_lock_retry(workspace):
    save_draft(workspace, sample())
    (workspace / "~$report.xlsx").write_bytes(b"locked")
    with pytest.raises(PermissionError):
        append_report(workspace, [sample()])
    assert len(pending(workspace)) == 1
    assert not (workspace / "report.xlsx").exists()
    (workspace / "~$report.xlsx").unlink()
    assert append_report(workspace, pending(workspace)) == (1, 0)


def test_atomic_replace_failure_keeps_old_report_and_draft(workspace):
    append_report(workspace, [sample()])
    before = (workspace / "report.xlsx").read_bytes()
    second = sample(file_hash="second", pixel_hash="second-p")
    save_draft(workspace, second)
    with patch("slipreport.storage.os.replace", side_effect=PermissionError("file open")):
        with pytest.raises(PermissionError):
            append_report(workspace, [second])
    assert (workspace / "report.xlsx").read_bytes() == before
    assert len(pending(workspace)) == 1
    assert not list(workspace.glob(".report-*.xlsx"))


def test_reject_unreviewed_and_invalid(workspace):
    with pytest.raises(ValueError):
        append_report(workspace, [sample(reviewed=False)])
    with pytest.raises(ValueError):
        append_report(workspace, [sample(date="2569-11-99")])
    assert not (workspace / "report.xlsx").exists()


def test_formula_injection_written_as_text(workspace):
    append_report(workspace, [sample(site='=HYPERLINK("https://example.com")', note="=1+1", recipient="+SUM(1,2)")])
    book = load_workbook(workspace / "report.xlsx")
    for address in ["B2", "C2", "F2"]:
        assert book[SHEET][address].data_type == "s"
    assert book[SHEET]["F2"].value == "=1+1"
    book.close()


def test_missing_ledger_refuses_silent_reimport(workspace):
    append_report(workspace, [sample()])
    book = load_workbook(workspace / "report.xlsx")
    del book[META]
    book.save(workspace / "report.xlsx")
    book.close()
    with pytest.raises(ValueError, match="ประวัติ"):
        imported(workspace)


def test_app_lock_releases(workspace):
    path = workspace / ".slipreport" / "app.lock"
    with FileLock(path):
        with pytest.raises(RuntimeError):
            FileLock(path).acquire()
    with FileLock(path):
        pass


def test_scan_cache_duplicate_corrupt_and_incremental(workspace):
    image = Image.new("RGB", (100, 150), "white")
    image.save(workspace / "data" / "original.png")
    shutil.copy2(workspace / "data" / "original.png", workspace / "data" / "renamed.png")
    info = PngImagePlugin.PngInfo()
    info.add_text("comment", "different metadata, same pixels")
    image.save(workspace / "data" / "metadata.png", pnginfo=info)
    (workspace / "data" / "broken.jpg").write_bytes(b"not a picture")
    image.close()
    calls = []

    def reader(_image, _rules):
        calls.append(1)
        return sample(reviewed=False)

    result = scan(workspace, reader=reader)
    assert result.added == 1
    assert result.duplicate == 2
    assert result.errors == 1
    assert len(calls) == 1
    draft = all_drafts(workspace)[0]
    draft.note = "แก้ไขเองแล้ว"
    draft.reviewed = True
    save_draft(workspace, draft)
    result = scan(workspace, reader=reader)
    assert result.added == 0
    assert len(calls) == 1
    assert all_drafts(workspace)[0].note == "แก้ไขเองแล้ว"
    assert append_report(workspace, pending(workspace)) == (1, 0)
    result = scan(workspace, reader=reader)
    assert result.added == 0
    assert len(calls) == 1
    with Image.new("RGB", (110, 150), "green") as new_image:
        new_image.save(workspace / "data" / "next.png")
    result = scan(workspace, reader=reader)
    assert result.added == 1
    assert len(pending(workspace)) == 1


def test_cancel_keeps_already_read(workspace):
    from threading import Event
    stop = Event()
    for i in range(3):
        with Image.new("RGB", (10 + i, 15), "white") as image:
            image.save(workspace / "data" / f"{i}.png")

    def reader(_image, _rules):
        stop.set()
        return sample(reviewed=False)
    result = scan(workspace, reader=reader, cancel=stop)
    assert result.added == 1
    assert result.cancelled
    assert len(pending(workspace)) == 1


def test_reread_fills_blanks_preserves_existing_values_and_skips_imported(workspace):
    from slipreport.ocr import file_hash, load_image, pixel_hash
    path = workspace / "data" / "test.png"
    with Image.new("RGB", (100, 150), "white") as image:
        image.save(path)
        pixels = pixel_hash(image)
    old = sample(file_hash=file_hash(path), pixel_hash=pixels, source="data/test.png", sender="", recipient="", site="", note="ผู้ใช้แก้ไว้", reviewed=False)
    save_draft(workspace, old)
    calls = []

    def reader(_image, _rules):
        calls.append(1)
        return sample(amount="999.00", note="อ่านใหม่", sender="กิตติ", recipient="ร้านทดสอบ", reviewed=False)

    assert scan(workspace, reader=reader).cached == 1
    assert not calls
    assert scan(workspace, reader=reader, reprocess=True).updated == 1
    reread = all_drafts(workspace)[0]
    assert reread.sender == "กิตติ"
    assert reread.recipient == "ร้านทดสอบ"
    assert reread.amount == "1250.50"
    assert reread.note == "ผู้ใช้แก้ไว้"
    assert reread.site == ""
    assert not reread.reviewed
    assert any("ต่างจากเดิม" in w for w in reread.warnings)
    assert list((workspace / ".slipreport" / "cache-backups").glob("*.sqlite3"))
    reread.reviewed = True
    append_report(workspace, [reread])
    assert scan(workspace, reader=reader, reprocess=True).updated == 0
    assert len(calls) == 1


def test_reread_failure_keeps_cached_values(workspace):
    from slipreport.ocr import file_hash
    path = workspace / "data" / "test.png"
    with Image.new("RGB", (100, 150), "white") as image:
        image.save(path)
    old = sample(file_hash=file_hash(path), sender="", reviewed=False)
    save_draft(workspace, old)

    def broken(_image, _rules):
        raise RuntimeError("OCR failed")
    assert scan(workspace, reader=broken, reprocess=True).errors == 1
    assert all_drafts(workspace)[0].to_dict() == old.to_dict()


def test_explicitly_cleared_fields_stay_blank():
    from slipreport.service import fill_missing
    old = sample(sender="", note="", reviewed=False, manual_fields=["note"])
    fresh = sample(sender="กิตติ", note="ข้อความจาก OCR")
    result = fill_missing(old, fresh)
    assert result.sender == "กิตติ"
    assert result.note == ""
