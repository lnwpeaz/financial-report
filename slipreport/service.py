from dataclasses import dataclass
from pathlib import Path
from threading import Event

from .extract import detect_site
from .model import Slip, OCR_VERSION
from .ocr import SUPPORTED, file_hash, load_image, pixel_hash, recognize
from .storage import all_drafts, backup_drafts, imported, read_sites, save_draft


@dataclass
class ScanResult:
    found: int = 0
    added: int = 0
    duplicate: int = 0
    cached: int = 0
    updated: int = 0
    errors: int = 0
    cancelled: bool = False


def fill_missing(existing, fresh):
    """Never overwrite a user's existing value, including an explicitly cleared cell."""
    notices = []
    for key in ["date", "sender", "recipient", "amount", "note"]:
        before, after = getattr(existing, key), getattr(fresh, key)
        if not before and key not in existing.manual_fields:
            setattr(existing, key, after)
        elif before and after and before != after and key in ["date", "amount"]:
            label = "วันที่" if key == "date" else "ยอดเงิน"
            notices.append(f"อ่านใหม่พบ{label}ต่างจากเดิม ({after}) โปรแกรมคงค่าเดิมไว้ กรุณาตรวจ")
    existing.raw_text = fresh.raw_text
    existing.reference = existing.reference or fresh.reference
    existing.confidence = fresh.confidence
    existing.warnings = [w for w in fresh.warnings if w not in ["อ่านวันที่ไม่ได้", "อ่านจำนวนเงินไม่ได้", "อ่านผู้รับไม่ได้", "อ่านผู้ส่งไม่ได้"]]
    existing.warnings.extend(notices)
    for label, key in [("วันที่", "date"), ("จำนวนเงิน", "amount"), ("ผู้รับ", "recipient"), ("ผู้ส่ง", "sender")]:
        if not getattr(existing, key):
            existing.warnings.append(f"อ่าน{label}ไม่ได้")
    return existing


def refresh_automatic(existing, fresh):
    """Refresh previous OCR, keeping each deliberate manual edit (including blanks)."""
    if existing.reviewed:
        return existing
    for key in ["date", "site", "sender", "recipient", "amount", "note"]:
        if key not in existing.manual_fields:
            setattr(existing, key, getattr(fresh, key))
    existing.raw_text = fresh.raw_text
    existing.reference = fresh.reference
    existing.confidence = fresh.confidence
    existing.warnings = list(fresh.warnings)
    existing.ocr_version = fresh.ocr_version
    return existing


def scan(root, emit=lambda *_: None, cancel=None, reader=recognize, reprocess=False, refresh=False):
    root = Path(root)
    cancel = cancel or Event()
    rules = read_sites(root)
    hashes, pixels, references = imported(root)
    cached = {s.file_hash: s for s in all_drafts(root)}
    if cached and (reprocess or refresh or any(s.ocr_version < OCR_VERSION and not s.reviewed for s in cached.values())):
        backup_drafts(root)
    known_pixels = pixels | {s.pixel_hash for s in cached.values()}
    files = sorted((p for p in (root / "data").rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED), key=lambda p: str(p).casefold())
    result = ScanResult(found=len(files))
    seen_hashes = set(hashes)
    for index, path in enumerate(files, 1):
        if cancel.is_set():
            result.cancelled = True
            break
        source = path.relative_to(root).as_posix()
        emit("progress", (index, len(files), source))
        try:
            if path.stat().st_size > 50 * 1024 * 1024:
                raise ValueError("ภาพใหญ่เกิน 50 MB กรุณาย่อภาพก่อน")
            digest = file_hash(path)
            if digest in seen_hashes:
                result.duplicate += 1
                continue
            seen_hashes.add(digest)
            if digest in cached:
                slip = cached[digest]
                slip.source = source
                update_auto = refresh or slip.ocr_version < OCR_VERSION
                if not slip.reviewed and (update_auto or (reprocess and any(not getattr(slip, key) for key in ["date", "sender", "recipient", "amount"]))):
                    with load_image(path) as picture:
                        fresh = reader(picture, rules)
                    if file_hash(path) != digest:
                        raise ValueError("ไฟล์เปลี่ยนระหว่างอ่าน กรุณารอคัดลอกให้เสร็จแล้วอ่านใหม่")
                    slip = refresh_automatic(slip, fresh) if update_auto else fill_missing(slip, fresh)
                    save_draft(root, slip)
                    result.updated += 1
                    emit("draft", slip)
                    continue
                # Preserve manual edits; only retry matching a previously empty site.
                if not slip.site and not slip.reviewed:
                    site, _ = detect_site(slip.raw_text, rules)
                    slip.site = site
                save_draft(root, slip)
                result.cached += 1
                continue
            image = load_image(path)
            try:
                pdigest = pixel_hash(image)
                if pdigest in known_pixels:
                    result.duplicate += 1
                    continue
                slip = reader(image, rules)
            finally:
                image.close()
            if file_hash(path) != digest:
                raise ValueError("ไฟล์เปลี่ยนระหว่างอ่าน กรุณารอคัดลอกให้เสร็จแล้วสแกนใหม่")
            slip.file_hash, slip.pixel_hash, slip.source = digest, pdigest, source
            if slip.reference and (slip.reference in references or any(s.reference == slip.reference for s in cached.values())):
                slip.warnings.append("เลขอ้างอิงซ้ำกับรายการอื่น กรุณาเช็กว่าเป็นสลิปเดียวกันหรือไม่")
            save_draft(root, slip)
            cached[digest] = slip
            known_pixels.add(pdigest)
            result.added += 1
            emit("draft", slip)
        except Exception as exc:
            result.errors += 1
            emit("error", f"{source}: {exc}")
    return result
