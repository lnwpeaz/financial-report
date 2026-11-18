"""Exercise real Tk button callbacks without taking control of the user's desktop."""
import os
import time

import pytest
from openpyxl import load_workbook
from slipreport.selftest import synthetic_image
from slipreport.storage import SHEET, initialize


@pytest.mark.skipif(os.name != "nt", reason="The packaged app targets Windows")
def test_real_ocr_review_blank_site_export_and_rescan(tmp_path, monkeypatch):
    from slipreport import gui
    initialize(tmp_path)
    synthetic_image(tmp_path / "data" / "สลิป.png")
    messages, errors = [], []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: messages.append(a))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: errors.append(a))
    app = gui.App(tmp_path)
    app.withdraw()

    def wait_idle():
        deadline = time.monotonic() + 15
        while app.busy and time.monotonic() < deadline:
            app.update()
            time.sleep(.02)
        app.update()
        assert not app.busy, "GUI worker did not finish"
        assert not errors, errors

    try:
        app.scan_button.invoke()
        wait_idle()
        assert len(app.slips) == 1
        assert app.fields["amount"].get() == "1250.50"
        # Reprocessing updates an existing tree row without duplicate identifiers.
        from slipreport.storage import save_draft
        existing = next(iter(app.slips.values()))
        existing.sender = ""
        save_draft(tmp_path, existing)
        app.reread_button.invoke()
        wait_idle()
        assert len(app.slips) == 1
        assert "Somchai" in next(iter(app.slips.values())).sender
        # User explicitly wants slips without a site to be included.
        app.fields["site"].set("")
        app.note.delete("1.0", "end")
        app.note.insert("1.0", "ตรวจยอดแล้ว")
        app.update()
        app.approve_button.invoke()
        app.update()
        assert next(iter(app.slips.values())).reviewed
        app.export_button.invoke()
        wait_idle()
        assert len(app.slips) == 0
        book = load_workbook(tmp_path / "report.xlsx")
        assert book[SHEET]["B2"].value is None
        assert book[SHEET]["E2"].value == 1250.5
        assert book[SHEET]["F2"].value == "ตรวจยอดแล้ว"
        book.close()
        app.scan_button.invoke()
        wait_idle()
        assert len(app.slips) == 0
        # Same Tk interpreter: Windows Tcl initialization is not reliable when
        # multiple Tk roots are destroyed/recreated in one pytest process.
        from slipreport.model import Slip
        from slipreport.storage import REVIEW
        partial=tmp_path/'partial'
        initialize(partial)
        save_draft(partial,Slip(file_hash='test-hash',pixel_hash='test-pixels',source='data/test.png',note='บันทึกที่อ่านได้'))
        app.open_workspace(partial)
        app.export_button.invoke()
        wait_idle()
        book=load_workbook(partial/'report.xlsx')
        assert book[SHEET]['F2'].value=='บันทึกที่อ่านได้'
        assert book[SHEET]['E2'].value is None
        assert book[REVIEW]['C2'].value=='ยังไม่ได้ตรวจ'
        book.close()
        assert len(app.history_rows) == 1
        assert "1" in app.saved_count.cget("text")
        assert "ไม่มีรายการ" in app.pending_count.cget("text")

        # One-click daily import from outside data; automatically copy + OCR + append.
        daily = tmp_path / "daily"
        initialize(daily)
        app.open_workspace(daily)
        source = tmp_path / "phone" / "สลิปวันนี้.png"
        source.parent.mkdir()
        synthetic_image(source)
        monkeypatch.setattr(gui.filedialog, "askopenfilenames", lambda **kwargs: (str(source),))
        app.import_files_button.invoke()
        wait_idle()
        assert source.exists()
        assert len(app.history_rows) == 1
        assert app.history_rows[0]["amount"] == 1250.5
        assert not app.slips
        assert "บันทึกสำเร็จ" in app.home_message.cget("text")
        assert app.tabs.select() == str(app.home)
        # Reimporting the same folder explains the no-op rather than looking broken.
        monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: str(source.parent))
        app.import_folder_button.invoke()
        wait_idle()
        assert len(app.history_rows) == 1
        assert "ไม่มีสลิปใหม่" in app.home_message.cget("text")
        app.history_search.set("Somchai")
        assert len(app.history_tree.get_children()) == 1

        # Day two: locked Excel keeps the pending row and permits a later retry.
        from PIL import Image
        Image.new("RGB", (50, 50), "blue").save(source.parent / "วันที่สอง.png")
        from slipreport.service import scan as real_scan
        monkeypatch.setattr(gui, "scan", lambda *a, **kw: real_scan(*a, **kw, reader=lambda *_: Slip(amount="250.00", note="วันที่สอง")))
        lock = daily / "~$report.xlsx"
        lock.write_bytes(b"locked by Excel")
        app.import_folder_button.invoke()
        deadline = time.monotonic() + 15
        while app.busy and time.monotonic() < deadline:
            app.update()
            time.sleep(.02)
        assert not app.busy
        assert len(errors) == 1
        errors.clear()
        assert len(app.slips) == 1
        assert len(app.history_rows) == 1
        lock.unlink()
        app.export_button.invoke()
        wait_idle()
        assert len(app.history_rows) == 2
        assert not app.slips
        app.history_search.set("")
        assert len(app.history_tree.get_children()) == 2
    finally:
        app.cancel.set()
        if app.lock:
            app.lock.release()
        app.destroy()
