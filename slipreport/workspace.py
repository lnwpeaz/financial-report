"""Daily imports and a read-only view of the user's single workbook."""
from dataclasses import dataclass, field
from datetime import date, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

from openpyxl import load_workbook

from .ocr import SUPPORTED, file_hash
from .storage import SHEET, check_workbook


@dataclass
class ImportResult:
    copied: int = 0
    duplicate: int = 0
    unsupported: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def import_images(root, selections, cancel=None, emit=lambda *_: None):
    """Copy, never move, originals. Snapshot folders before writing into data.

    A fresh batch directory preserves identical names from separate selections.
    File hashes avoid copying a whole old folder every day. Pixel deduplication
    remains in scan(), so differently encoded copies do not create Excel rows.
    """
    root = Path(root).resolve()
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    result = ImportResult()
    sources, seen_paths = [], set()
    for selection in selections:
        selected = Path(selection).resolve()
        if not selected.exists():
            result.errors.append(f"ไม่พบไฟล์หรือโฟลเดอร์: {selected}")
            continue
        candidates = sorted(selected.rglob("*")) if selected.is_dir() else [selected]
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            if path.suffix.lower() not in SUPPORTED:
                result.unsupported += 1
                continue
            sources.append(path)
    hashes = set()
    for path in data.rglob("*"):
        if cancel and cancel.is_set():
            result.cancelled = True
            return result
        if path.is_file() and path.suffix.lower() in SUPPORTED:
            try:
                hashes.add(file_hash(path))
            except OSError:
                pass  # scan will report unreadable existing files separately.
    batch = data / date.today().isoformat() / (datetime.now().strftime("%H%M%S") + "-" + uuid.uuid4().hex[:6])
    for index, source in enumerate(sources, 1):
        if cancel and cancel.is_set():
            result.cancelled = True
            break
        emit("copy_progress", (index, len(sources), source.name))
        temporary = None
        try:
            if source.stat().st_size > 50 * 1024 * 1024:
                raise ValueError("ภาพใหญ่เกิน 50 MB กรุณาย่อภาพก่อน")
            digest = file_hash(source)
            if digest in hashes:
                result.duplicate += 1
                continue
            batch.mkdir(parents=True, exist_ok=True)
            # Same names from several folders must never replace each other.
            target = batch / source.name
            if target.exists():
                target = batch / f"{source.stem}-{uuid.uuid4().hex[:8]}{source.suffix}"
            temporary = batch / ("." + uuid.uuid4().hex + ".part")
            shutil.copyfile(source, temporary)
            if file_hash(temporary) != digest or file_hash(source) != digest:
                raise ValueError("ไฟล์กำลังเปลี่ยน กรุณารอให้คัดลอกหรือดาวน์โหลดเสร็จแล้วเพิ่มใหม่")
            os.replace(temporary, target)
            hashes.add(digest)
            result.copied += 1
        except Exception as exc:
            result.errors.append(f"{source.name}: {exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return result


def report_rows(root):
    """Read visible cells, not OCR cache: manual Excel edits remain visible."""
    path = Path(root) / "report.xlsx"
    if not path.exists():
        return []
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        check_workbook(book)
        rows = []
        for number, values in enumerate(book[SHEET].iter_rows(min_row=2, max_col=6, values_only=True), 2):
            if all(value is None for value in values):
                continue
            day, site, recipient, sender, amount, note = values
            if isinstance(day, (date, datetime)):
                day = day.strftime("%Y-%m-%d")
            rows.append({"row": number, "date": day or "", "site": site or "", "recipient": recipient or "", "sender": sender or "", "amount": amount, "note": note or ""})
        return rows
    finally:
        book.close()


def workspace_parent(path):
    """Selecting data is a common mistake: keep using its existing report."""
    path = Path(path).resolve()
    if (path / "report.xlsx").exists():
        return path
    for parent in reversed(path.parents):
        parts = path.relative_to(parent).parts
        if parts and parts[0].casefold() == "data" and (parent / ".slipreport" / "cache.sqlite3").exists():
            return parent
    return path


def remembered_workspace(default, settings):
    settings = Path(settings)
    if not settings.exists():
        return Path(default).resolve()
    value = json.loads(settings.read_text(encoding="utf-8"))
    saved = value.get("workspace")
    if not saved:
        return Path(default).resolve()
    path = Path(saved)
    if not path.is_absolute():
        path = settings.parent / path
    if not path.is_dir():
        raise ValueError(f"ไม่พบโฟลเดอร์งานที่จำไว้: {path}\nกรุณาเชื่อมต่อไดรฟ์เดิม หรือลบ app-settings.json เพื่อใช้โฟลเดอร์ข้างโปรแกรม")
    return path.resolve()


def remember_workspace(path, settings):
    settings = Path(settings)
    settings.parent.mkdir(parents=True, exist_ok=True)
    resolved = Path(path).resolve()
    # Keep the standard portable folder working after copying to another PC.
    value = "." if resolved == settings.parent.resolve() else str(resolved)
    temporary = settings.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps({"workspace": value}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, settings)
    finally:
        temporary.unlink(missing_ok=True)
