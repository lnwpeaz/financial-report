from pathlib import Path
from threading import Event

from PIL import Image
from openpyxl import load_workbook
import pytest

from slipreport.model import Slip
from slipreport.service import scan
from slipreport.storage import initialize, append_report, pending, SHEET
from slipreport.workspace import import_images, report_rows, remember_workspace, remembered_workspace, workspace_parent


def picture(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 50), color).save(path)
    return path


def test_daily_folder_copy_dedup_preserves_originals_and_excel_edits(tmp_path):
    work = tmp_path / "งาน"
    initialize(work)
    source = tmp_path / "จากมือถือ"
    first = picture(source / "วันแรก" / "slip.png", "red")
    second = picture(source / "วันสอง" / "slip.png", "blue")
    (source / "ไฟล์.pdf").write_bytes(b"not supported")
    before = {p: p.read_bytes() for p in (first, second)}
    copied = import_images(work, [source])
    assert (copied.copied, copied.unsupported, copied.errors) == (2, 1, [])
    assert len(list((work / "data").rglob("*.png"))) == 2
    assert all(p.read_bytes() == content for p, content in before.items())
    reader = lambda image, rules: Slip(amount="100.00", note="SI", site="สยาม")
    assert scan(work, reader=reader).added == 2
    assert append_report(work, pending(work), allow_unreviewed=True) == (2, 0)
    book = load_workbook(work / "report.xlsx")
    book[SHEET]["F2"] = "แก้ใน Excel เอง"
    book.save(work / "report.xlsx")
    book.close()
    third = picture(source / "วันสาม" / "slip.png", "green")
    again = import_images(work, [source])
    assert (again.copied, again.duplicate) == (1, 2)
    assert scan(work, reader=reader).added == 1
    assert append_report(work, pending(work), allow_unreviewed=True) == (1, 0)
    rows = report_rows(work)
    assert len(rows) == 3
    assert rows[0]["note"] == "แก้ใน Excel เอง"
    assert third.exists()
    assert list((work / "backups").glob("*.xlsx"))
    assert pending(work) == []
    # Selecting our own data folder doesn't recursively duplicate it.
    repeat = import_images(work, [work / "data"])
    assert repeat.copied == 0 and repeat.duplicate == 3


def test_copy_cancel_missing_and_damaged_files(tmp_path):
    work = tmp_path / "work"
    initialize(work)
    source = picture(tmp_path / "original.png", "red")
    stop = Event()
    stop.set()
    assert import_images(work, [source], cancel=stop).cancelled
    assert not list((work / "data").rglob("*.png"))
    assert import_images(work, [tmp_path / "missing.png"]).errors
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"broken")
    assert import_images(work, [broken]).copied == 1
    assert scan(work).errors == 1
    assert not (work / "report.xlsx").exists()


def test_remember_workspace_and_portable_defaults(tmp_path):
    default = tmp_path / "app"
    default.mkdir()
    settings = default / "app-settings.json"
    assert remembered_workspace(default, settings) == default.resolve()
    remember_workspace(default, settings)
    # '.' remains relative to the portable program when moved.
    moved = tmp_path / "another computer"
    moved.mkdir()
    moved_settings = moved / settings.name
    moved_settings.write_bytes(settings.read_bytes())
    assert remembered_workspace(moved, moved_settings) == moved.resolve()
    external = tmp_path / "งานอื่น"
    external.mkdir()
    remember_workspace(external, settings)
    assert remembered_workspace(default, settings) == external.resolve()
    external.rmdir()
    with pytest.raises(ValueError, match="ไม่พบโฟลเดอร์งาน"):
        remembered_workspace(default, settings)


def test_unreadable_report_is_not_mistaken_for_empty(tmp_path):
    initialize(tmp_path)
    assert report_rows(tmp_path) == []
    (tmp_path / "report.xlsx").write_bytes(b"damaged")
    with pytest.raises(Exception):
        report_rows(tmp_path)


def test_selecting_data_does_not_create_a_nested_workspace(tmp_path):
    initialize(tmp_path)
    day = tmp_path / "data" / "วันนี้"
    day.mkdir()
    assert workspace_parent(tmp_path / "data") == tmp_path.resolve()
    assert workspace_parent(day) == tmp_path.resolve()
    initialize(tmp_path / "data")  # Repair selection even with an old empty nested cache.
    assert workspace_parent(tmp_path / "data" / "data") == tmp_path.resolve()
    another = tmp_path / "งานแยก"
    assert workspace_parent(another) == another.resolve()
