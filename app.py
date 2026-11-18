import argparse
import ctypes
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="Offline Thai slips to Excel")
    parser.add_argument("--workspace", type=Path, help="Folder containing data/ and report.xlsx")
    parser.add_argument("--self-test", type=Path, metavar="JSON_PATH", help="Run bundled OCR + Excel smoke test and write a JSON result")
    parser.add_argument("--scan", action="store_true", help="Read new slips into the review queue without opening the GUI")
    parser.add_argument("--export", action="store_true", help="With --scan, append all OCR results to Excel including incomplete/unreviewed rows")
    parser.add_argument("--refresh", action="store_true", help="With --scan, refresh automatic fields on unexported, unreviewed slips")
    args = parser.parse_args()
    root = args.workspace or (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
    if args.self_test:
        from slipreport.selftest import run
        result = run()
        args.self_test.parent.mkdir(parents=True, exist_ok=True)
        args.self_test.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["ok"] else 1
    if args.scan:
        from dataclasses import asdict
        from slipreport.service import scan
        from slipreport.storage import FileLock, initialize, append_report, pending
        initialize(root)
        with FileLock(root / ".slipreport" / "app.lock"):
            result = scan(root, refresh=args.refresh)
            if args.export:
                append_report(root, pending(root), allow_unreviewed=True)
        if sys.stdout:
            print(json.dumps(asdict(result), ensure_ascii=False))
        return 1 if result.errors else 0
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        from slipreport.gui import App
        from slipreport.workspace import remembered_workspace
        settings_path = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent) / "app-settings.json"
        if not args.workspace:
            root = remembered_workspace(root, settings_path)
        app = App(root, settings_path=settings_path if not args.workspace else None)
        app.mainloop()
        return 0
    except Exception as exc:
        from tkinter import Tk, messagebox
        root_window = Tk()
        root_window.withdraw()
        messagebox.showerror("เปิดโปรแกรมไม่ได้", f"{exc}\n\nกรุณาแตก ZIP ลงโฟลเดอร์ที่เขียนไฟล์ได้ เช่น Documents หรือ Desktop")
        root_window.destroy()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
