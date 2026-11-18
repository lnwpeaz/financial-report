import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .extract import detect_site
from .model import money
from .ocr import load_image
from .service import scan
from .storage import FileLock, append_report, initialize, pending, read_sites, save_draft, save_sites
from .workspace import import_images, report_rows, remember_workspace, workspace_parent
from . import __version__

BG = "#F3F6F5"
INK = "#193D43"
MUTED = "#64777A"
TEAL = "#136F63"


class App(tk.Tk):
    def __init__(self, root, settings_path=None):
        super().__init__()
        self.title("สลิปเป็น Excel • ใช้รายงานเดิมได้ทุกวัน")
        width = min(1380, self.winfo_screenwidth() - 60)
        height = min(860, self.winfo_screenheight() - 100)
        self.geometry(f"{width}x{height}")
        self.minsize(1050, 620)
        self.configure(bg=BG)
        self.root_path = Path(root).resolve()
        self.settings_path = settings_path
        self.history_rows = []
        self.history_error = ""
        self.auto_export = False
        self.import_summary = ""
        self.scan_notice = ""
        self.lock = None
        self.slips = {}
        self.current = None
        self.dirty = False
        self.loading = False
        self.busy = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.photo = None
        self.errors = []
        self.watch = tk.BooleanVar(value=False)
        self._style()
        self._layout()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-Return>", lambda _: self.approve())
        self.bind("<Control-s>", lambda _: self.export())
        self.open_workspace(self.root_path)
        self.after(120, self.poll)
        self.after(15000, self.watch_tick)

    def _style(self):
        self.option_add("*Font", ("Tahoma", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background=BG, foreground=INK, font=("Tahoma", 10))
        style.configure("Card.TLabel", background="white", foreground=INK)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TButton", padding=(12, 9), font=("Tahoma", 10))
        style.configure("Accent.TButton", background=TEAL, foreground="white", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#0F5B51"), ("disabled", "#A0B9B3")])
        style.configure("Treeview", font=("Tahoma", 10), rowheight=34, background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", font=("Tahoma", 9, "bold"), background="#E3EEEB", padding=(7, 9))
        style.map("Treeview", background=[("selected", "#D3EAE3")], foreground=[("selected", INK)])
        style.configure("TEntry", padding=7)
        style.configure("TCombobox", padding=7)
        style.configure("TCheckbutton", background=BG, foreground=INK)

    def _layout(self):
        header = tk.Frame(self, bg=INK, padx=24, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="สลิป → Excel", bg=INK, fg="white", font=("Tahoma", 23, "bold")).pack(side="left")
        tk.Label(header, text="เพิ่มสลิปวันนี้ • เก็บต่อใน Excel เดิม", bg=INK, fg="#BFD9D2", font=("Tahoma", 11)).pack(side="left", padx=28)
        tk.Label(header, text=f"ออฟไลน์  /  v{__version__}", bg=INK, fg="#BFD9D2", font=("Tahoma", 9)).pack(side="right")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(12, 0))
        self.home = ttk.Frame(self.tabs, padding=22)
        self.review_page = ttk.Frame(self.tabs)
        self.history_page = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(self.home, text="  หน้าหลัก / เพิ่มสลิป  ")
        self.tabs.add(self.review_page, text="  รอลง Excel / ตรวจแก้  ")
        self.tabs.add(self.history_page, text="  ประวัติใน Excel  ")
        self._home_layout()
        self._history_layout()

        bar = ttk.Frame(self.review_page, padding=(14, 10))
        bar.pack(fill="x")
        self.scan_button = ttk.Button(bar, text="1  อ่านสลิปใน data", style="Accent.TButton", command=self.start_scan)
        self.scan_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="หยุด", command=self.stop_scan, state="disabled")
        self.stop_button.pack(side="left", padx=(6, 14))
        ttk.Button(bar, text="เปิดโฟลเดอร์ data", command=lambda: self.open_path(self.root_path / "data")).pack(side="left", padx=3)
        ttk.Button(bar, text="เปิด Excel", command=self.open_report).pack(side="left", padx=3)
        self.sites_button = ttk.Button(bar, text="ตั้งชื่อไซต์ / คำย่อ", command=self.edit_sites)
        self.sites_button.pack(side="left", padx=3)
        self.workspace_button = ttk.Button(bar, text="เปลี่ยนโฟลเดอร์งาน", command=self.choose_workspace)
        self.workspace_button.pack(side="right")

        pathbar = ttk.Frame(self.review_page, padding=(16, 0, 16, 8))
        pathbar.pack(fill="x")
        ttk.Label(pathbar, text="ตรวจแก้ก่อนบันทึกได้ หรือกด ‘บันทึกรายการที่รอ’ โดยไม่ต้องยืนยันทีละใบ", style="Muted.TLabel").pack(side="left")

        panes = ttk.Panedwindow(self.review_page, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=20)
        left = ttk.Frame(panes, style="Card.TFrame", padding=12)
        panes.add(left, weight=3)
        self.count_label = ttk.Label(left, text="รายการรอตรวจ", style="Card.TLabel", font=("Tahoma", 12, "bold"))
        self.count_label.pack(anchor="w", pady=(0, 10))
        treeframe = ttk.Frame(left)
        treeframe.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(treeframe, columns=("file", "amount", "state"), show="headings", selectmode="browse")
        for key, text, width in [("file", "ไฟล์สลิป", 165), ("amount", "บาท", 82), ("state", "สถานะ", 75)]:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=55, stretch=key == "file", anchor="e" if key == "amount" else "w")
        scroll = ttk.Scrollbar(treeframe, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.tree.tag_configure("ready", foreground=TEAL)
        self.reread_button = ttk.Button(left, text="อ่านใหม่เฉพาะรายการที่รอ", command=lambda: self.start_scan(reprocess=True))
        self.reread_button.pack(fill="x", pady=(8, 0))
        self.approve_all_button = ttk.Button(left, text="ทำเครื่องหมายว่าตรวจแล้วทั้งหมด…", command=self.approve_all)
        self.approve_all_button.pack(fill="x", pady=(12, 0))

        preview_card = ttk.Frame(panes, style="Card.TFrame", padding=12)
        panes.add(preview_card, weight=4)
        ttk.Label(preview_card, text="ภาพต้นฉบับ", style="Card.TLabel", font=("Tahoma", 12, "bold")).pack(anchor="w")
        ttk.Label(preview_card, text="คลิกภาพเพื่อเปิดดูขนาดจริง", style="Card.TLabel", foreground=MUTED).pack(anchor="w", pady=(4, 8))
        self.preview = tk.Label(preview_card, bg="#EAF0EE", text="นำรูปใส่ในโฟลเดอร์ data\nแล้วกด ‘อ่านสลิป’", fg=MUTED, cursor="hand2")
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<Button-1>", lambda _: self.open_source())
        self.preview.bind("<Configure>", self.preview_resize)
        self.preview_job = None

        edit_card = ttk.Frame(panes, style="Card.TFrame")
        panes.add(edit_card, weight=4)
        editor_canvas = tk.Canvas(edit_card, bg="white", highlightthickness=0, width=410)
        editor_scroll = ttk.Scrollbar(edit_card, orient="vertical", command=editor_canvas.yview)
        editor_canvas.configure(yscrollcommand=editor_scroll.set)
        editor_scroll.pack(side="right", fill="y")
        editor_canvas.pack(side="left", fill="both", expand=True)
        edit = ttk.Frame(editor_canvas, style="Card.TFrame", padding=16)
        editor_id = editor_canvas.create_window((0, 0), window=edit, anchor="nw")
        edit.bind("<Configure>", lambda _: editor_canvas.configure(scrollregion=editor_canvas.bbox("all")))
        editor_canvas.bind("<Configure>", lambda event: editor_canvas.itemconfigure(editor_id, width=event.width))
        self.panes = panes
        self.panes_positioned = False
        self.tabs.bind("<<NotebookTabChanged>>", self.tab_changed)
        ttk.Label(edit, text="ตรวจแก้ข้อมูล (ข้ามไป Excel ได้)", style="Card.TLabel", font=("Tahoma", 12, "bold")).pack(anchor="w")
        self.warning = tk.Label(edit, text="เลือกสลิปทางซ้ายเพื่อเริ่มตรวจ", bg="#FFF5DA", fg="#795B13", justify="left", anchor="w", wraplength=330, padx=10, pady=9, font=("Tahoma", 9))
        self.warning.pack(fill="x", pady=(10, 8))
        self.fields = {}
        self.field_widgets = []
        for key, label in [("date", "วันที่ (ค.ศ. YYYY-MM-DD)"), ("site", "ไซต์งาน (ถ้าไม่มี เว้นว่างได้)"), ("recipient", "ผู้รับ"), ("sender", "ผู้ส่ง"), ("amount", "จำนวนเงิน (บาท)")]:
            ttk.Label(edit, text=label, style="Card.TLabel").pack(anchor="w", pady=(5, 3))
            var = tk.StringVar()
            var.trace_add("write", self.on_edit)
            self.fields[key] = var
            if key == "site":
                self.site_combo = ttk.Combobox(edit, textvariable=var)
                self.site_combo.pack(fill="x")
                self.field_widgets.append(self.site_combo)
            else:
                entry = ttk.Entry(edit, textvariable=var)
                entry.pack(fill="x")
                self.field_widgets.append(entry)
        ttk.Label(edit, text="หมายเหตุ / บันทึกความจำ", style="Card.TLabel").pack(anchor="w", pady=(7, 3))
        self.note = tk.Text(edit, width=34, height=3, wrap="word", relief="solid", borderwidth=1, font=("Tahoma", 10), padx=7, pady=5, undo=True)
        self.note.pack(fill="both", expand=True)
        self.note.bind("<<Modified>>", self.note_changed)
        actions = ttk.Frame(edit, style="Card.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="ดูข้อความ OCR", command=self.show_ocr).pack(side="left")
        self.approve_button = ttk.Button(actions, text="ยืนยัน → ถัดไป", style="Accent.TButton", command=self.approve)
        self.approve_button.pack(side="right")
        def editor_wheel(event):
            editor_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        self.bind_class("SlipEditor", "<MouseWheel>", editor_wheel)
        def tag_editor(widget):
            widget.bindtags(("SlipEditor", *widget.bindtags()))
            for child in widget.winfo_children():
                tag_editor(child)
        tag_editor(edit)

        bottom = ttk.Frame(self, padding=(22, 14))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="พร้อมใช้งาน", style="Muted.TLabel", wraplength=680)
        self.status.pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(bottom, length=100, mode="indeterminate")
        self.stop_main = ttk.Button(bottom, text="หยุด", command=self.stop_scan, state="disabled")
        self.log_button = ttk.Button(bottom, text="ดูบันทึก", command=self.show_log)
        self.log_button.pack(side="left", padx=10)
        self.export_button = ttk.Button(bottom, text="บันทึกรายการที่รอ (0)", style="Accent.TButton", command=self.export)
        self.export_button.pack(side="right")

    def _home_layout(self):
        canvas = tk.Canvas(self.home, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(self.home, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        content = ttk.Frame(canvas)
        item = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        ttk.Label(content, text="ทุกวัน แค่เพิ่มรูปสลิป", font=("Tahoma", 22, "bold")).pack(anchor="w")
        ttk.Label(content, text="เลือกรูปหรือโฟลเดอร์ → โปรแกรมอ่านและเพิ่มต่อท้าย report.xlsx ให้ทันที", font=("Tahoma", 12)).pack(anchor="w", pady=(6, 16))
        actions = ttk.Frame(content)
        actions.pack(fill="x")
        self.import_files_button = ttk.Button(actions, text="＋ เพิ่มรูปสลิป…", style="Accent.TButton", command=self.choose_images)
        self.import_files_button.pack(side="left", ipadx=15, ipady=12)
        self.import_folder_button = ttk.Button(actions, text="＋ เพิ่มทั้งโฟลเดอร์…", command=self.choose_import_folder)
        self.import_folder_button.pack(side="left", padx=12, ipadx=10, ipady=12)
        self.home_report_button = ttk.Button(actions, text="เปิด Excel ของฉัน", command=self.open_report)
        self.home_report_button.pack(side="left", ipadx=10, ipady=12)
        ttk.Label(content, text="เลือกรูปได้หลายใบ รวมโฟลเดอร์ย่อยได้ • โปรแกรมคัดลอกเก็บให้ ต้นฉบับยังอยู่ที่เดิม", style="Muted.TLabel").pack(anchor="w", pady=(10, 18))

        card = ttk.Frame(content, style="Card.TFrame", padding=18)
        card.pack(fill="x")
        self.saved_count = ttk.Label(card, text="", style="Card.TLabel", font=("Tahoma", 19, "bold"))
        self.saved_count.pack(anchor="w")
        self.pending_count = ttk.Label(card, text="", style="Card.TLabel", font=("Tahoma", 11))
        self.pending_count.pack(anchor="w", pady=(6, 10))
        self.home_message = tk.Label(card, text="", bg="white", fg=TEAL, font=("Tahoma", 11), justify="left", anchor="w", wraplength=950)
        self.home_message.pack(fill="x")
        self.path_label = ttk.Label(card, text="", style="Card.TLabel", foreground=MUTED, wraplength=950)
        self.path_label.pack(anchor="w", pady=(10, 0))

        ttk.Label(content, text="วันถัดไปใช้ปุ่มเดิมได้เลย ไม่ต้องลบสลิปเก่า ไม่ต้องสร้าง Excel ใหม่", font=("Tahoma", 12, "bold")).pack(anchor="w", pady=(20, 6))
        ttk.Label(content, text="ปิดไฟล์ Excel ก่อนเพิ่มสลิป • รูปเดิมจะข้ามให้ • ข้อมูลที่อ่านไม่ได้จะเว้นว่างและระบุในชีต ‘ตรวจสอบ’").pack(anchor="w")
        ttk.Label(content, text="ไซต์งานอ่านจากบันทึก เช่น SI → สยาม, นนท์ → นนท์; ถ้าไม่พบไซต์จะเว้นว่าง").pack(anchor="w", pady=(5, 12))
        extra = ttk.Frame(content)
        extra.pack(fill="x")
        self.home_scan = ttk.Button(extra, text="ใส่รูปใน data แล้ว? อ่านและบันทึก", command=lambda: self.start_scan(auto_export=True))
        self.home_scan.pack(side="left")
        ttk.Button(extra, text="เปิดโฟลเดอร์งาน", command=lambda: self.open_path(self.root_path)).pack(side="left", padx=8)
        ttk.Button(extra, text="วิธีใช้ / ส่งให้เครื่องอื่น", command=self.show_help).pack(side="left")
        self.auto_check = ttk.Checkbutton(content, text="อ่านและบันทึกรูปใหม่ใน data ทุก 15 วินาที ขณะเปิดโปรแกรม", variable=self.watch)
        self.auto_check.pack(anchor="w", pady=(12, 0))
        settings = ttk.Menubutton(content, text="ตั้งค่า / โฟลเดอร์งาน")
        menu = tk.Menu(settings, tearoff=False)
        menu.add_command(label="ชื่อไซต์ / คำย่อ", command=self.edit_sites)
        menu.add_command(label="เปลี่ยนโฟลเดอร์งาน (ใช้ Excel อีกชุด)", command=self.choose_workspace)
        menu.add_command(label="เปิดรูปที่โปรแกรมเก็บไว้", command=lambda: self.open_path(self.root_path / "data"))
        settings["menu"] = menu
        settings.pack(anchor="w", pady=(12, 5))

    def _history_layout(self):
        top = ttk.Frame(self.history_page)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="รายการที่บันทึกลง Excel แล้ว", font=("Tahoma", 15, "bold")).pack(side="left")
        self.history_refresh = ttk.Button(top, text="รีเฟรชจาก Excel", command=self.reload_history)
        self.history_refresh.pack(side="right")
        ttk.Button(top, text="เปิด Excel เพื่อแก้ไข", command=self.open_report).pack(side="right", padx=8)
        filters = ttk.Frame(self.history_page)
        filters.pack(fill="x", pady=(0, 10))
        ttk.Label(filters, text="ค้นหา วันที่ / ไซต์ / ชื่อ / บันทึก").pack(side="left")
        self.history_search = tk.StringVar()
        self.history_search.trace_add("write", lambda *_: self.filter_history())
        ttk.Entry(filters, textvariable=self.history_search, width=35).pack(side="left", padx=10)
        self.history_label = ttk.Label(filters, text="", style="Muted.TLabel")
        self.history_label.pack(side="left")
        holder = ttk.Frame(self.history_page)
        holder.pack(fill="both", expand=True)
        self.history_tree = ttk.Treeview(holder, columns=("row", "date", "site", "recipient", "sender", "amount", "note"), show="headings")
        for key, label, width in [("row", "แถว", 45), ("date", "วันที่", 100), ("site", "ไซต์", 100), ("recipient", "ผู้รับ", 200), ("sender", "ผู้ส่ง", 170), ("amount", "บาท", 110), ("note", "บันทึก", 250)]:
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, minwidth=40, stretch=True, anchor="e" if key == "amount" else "w")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.history_tree.yview)
        scroll.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(holder, orient="horizontal", command=self.history_tree.xview)
        horizontal.pack(side="bottom", fill="x")
        self.history_tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<Double-1>", self.history_detail)
        ttk.Label(self.history_page, text="ดับเบิลคลิกเพื่ออ่านข้อความเต็ม • ข้อมูลหน้านี้อ่านจาก Excel ปัจจุบัน • แก้ข้อมูลที่บันทึกแล้วได้ใน Excel", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

    def tab_changed(self, _=None):
        if self.tabs.select() == str(self.review_page) and not self.panes_positioned:
            self.update_idletasks()
            width = self.panes.winfo_width()
            self.panes.sashpos(0, int(width * .27))
            self.panes.sashpos(1, int(width * .61))
            self.panes_positioned = True

    def reload_history(self):
        try:
            self.history_rows = report_rows(self.root_path)
            self.history_error = ""
        except Exception as exc:
            self.history_rows = []
            self.history_error = str(exc)
        self.filter_history()
        self.update_count()

    def filter_history(self):
        query = self.history_search.get().strip().casefold()
        self.history_tree.delete(*self.history_tree.get_children())
        for row in reversed(self.history_rows):
            if query and query not in " ".join(str(v) for v in row.values()).casefold():
                continue
            values = [row[k] for k in ("row", "date", "site", "recipient", "sender", "amount", "note")]
            if isinstance(row["amount"], (int, float)):
                values[5] = f"{row['amount']:,.2f}"
            elif row["amount"] is None:
                values[5] = "—"
            values = [str(v).replace("\n", " ") for v in values]
            self.history_tree.insert("", "end", iid=str(row["row"]), values=values)
        self.history_label.configure(text=self.history_error or f"แสดง {len(self.history_tree.get_children())} / {len(self.history_rows)} รายการ")

    def history_detail(self, _=None):
        selected = self.history_tree.selection()
        if not selected:
            return
        row = next(r for r in self.history_rows if str(r["row"]) == selected[0])
        labels = {"row": "แถวใน Excel", "date": "วันที่", "site": "ไซต์", "recipient": "ผู้รับ", "sender": "ผู้ส่ง", "amount": "จำนวนเงิน", "note": "บันทึก"}
        self.text_window("ข้อมูลที่บันทึกแล้ว", "\n\n".join(f"{label}: {row[key] if row[key] is not None else ''}" for key, label in labels.items()))

    def choose_images(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self, title="เลือกรูปสลิปวันนี้ (เลือกหลายรูปได้)", filetypes=[("รูปสลิป", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"), ("ทุกไฟล์", "*.*")])
        if paths:
            self.start_scan(auto_export=True, selections=paths)

    def choose_import_folder(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self, title="เลือกโฟลเดอร์สลิป โปรแกรมจะรวมรูปในโฟลเดอร์ย่อยให้ด้วย")
        if path:
            self.start_scan(auto_export=True, selections=[path])

    def show_help(self):
        self.text_window("ใช้ทุกวัน / ย้ายเครื่อง", "ทุกวันทำเพียง 3 ขั้นตอน\n\n1. ปิด report.xlsx ถ้าเปิดอยู่\n2. กด ‘เพิ่มรูปสลิป’ หรือ ‘เพิ่มทั้งโฟลเดอร์’ แล้วเลือกรูป\n3. รอข้อความบันทึกสำเร็จ แล้วกด ‘เปิด Excel ของฉัน’\n\nโปรแกรมคัดลอกรูปไป data แยกตามวันที่นำเข้า แล้วอ่านและเพิ่มต่อท้าย report.xlsx ไฟล์เดิม วันที่ใน Excel ใช้วันที่บนสลิป ไม่ใช่วันที่นำเข้า\n\nไม่ต้องลบรูปเก่า สลิปที่ไฟล์หรือภาพเหมือนเดิมจะข้ามให้ ถ้าถ่ายใหม่/ครอปใหม่อาจไม่ถือเป็นภาพซ้ำ ให้ตรวจเลขอ้างอิงซ้ำในชีตตรวจสอบด้วย\n\nถ้าไม่มีไซต์หรืออ่านบางช่องไม่ได้ รายการยังเข้า Excel ได้และช่องนั้นจะว่าง ตรวจแก้ภายหลังใน Excel ได้ อย่าเปลี่ยนหัวคอลัมน์หรือลบชีตประวัติที่ซ่อนอยู่\n\nเมื่อบันทึกแล้ว รายการจะย้ายจาก ‘รอลง Excel’ ไป ‘ประวัติใน Excel’ จึงไม่ใช่ข้อมูลหาย\n\nสำรองงาน: ปิดโปรแกรมและ Excel แล้วคัดลอกโฟลเดอร์งานทั้งโฟลเดอร์ รวม data, report.xlsx, sites.json และ .slipreport ไปเก็บอีกไดรฟ์ โปรแกรมเก็บ Excel รุ่นก่อนหน้าใน backups ให้อีกชั้น\n\nส่งโปรแกรมเปล่าให้คนอื่น: ส่ง SlipToExcel-Windows-x64.zip ให้แตก ZIP ก่อนเปิด EXE ไม่ต้องติดตั้ง Python หรือมี API key\n\nย้ายงานเดิมไปเครื่องอื่น: ปิดโปรแกรมและ Excel แล้วคัดลอกโฟลเดอร์งานทั้งโฟลเดอร์พร้อม EXE อย่าส่งเฉพาะ EXE หากต้องการรายงานและรูปเดิมไปด้วย")

    def open_workspace(self, path):
        path = workspace_parent(path)
        initialize(path)
        if self.lock and self.root_path == path:
            return
        new_lock = FileLock(path / ".slipreport" / "app.lock").acquire()
        try:
            rules = read_sites(path)
            drafts = pending(path)
        except Exception:
            new_lock.release()
            raise
        if self.lock:
            self.lock.release()
        self.lock = new_lock
        self.root_path = path
        self.current = None
        self.dirty = False
        self.watch.set(False)
        self.slips = {s.file_hash: s for s in drafts}
        self.site_combo["values"] = [r["name"] for r in rules]
        self.path_label.configure(text=f"Excel ที่ใช้ต่อทุกวัน: {path / 'report.xlsx'}")
        self.reload_history()
        self.refresh()
        self.home_message.configure(text="เลือกรูปเพิ่มได้เลย รายการเดิมอยู่ในแท็บ ‘ประวัติใน Excel’" if self.history_rows else "พร้อมเริ่มใช้งาน เลือกรูปสลิป โปรแกรมจะสร้าง Excel ให้ครั้งแรก")

    def choose_workspace(self):
        if self.busy:
            return
        self.save_current()
        chosen = filedialog.askdirectory(title="เลือกโฟลเดอร์งาน (โปรแกรมจะสร้าง data และ report.xlsx ภายใน)", initialdir=self.root_path)
        if chosen:
            try:
                self.open_workspace(chosen)
                if self.settings_path:
                    remember_workspace(self.root_path, self.settings_path)
                self.tabs.select(self.home)
            except Exception as exc:
                self.show_error(exc)

    def refresh(self, select=None):
        previous = select or self.current
        self.tree.delete(*self.tree.get_children())
        for key, slip in self.slips.items():
            try:
                amount = f"{money(slip.amount):,.2f}"
            except ValueError:
                amount = "—"
            self.tree.insert("", "end", iid=key, values=(Path(slip.source).name, amount, "ตรวจแล้ว" if slip.reviewed else "รอตรวจ"), tags=("ready",) if slip.reviewed else ())
        self.update_count()
        keys = list(self.slips)
        if keys:
            selected = previous if previous in self.slips else keys[0]
            self.current = None
            self.tree.selection_set(selected)
            self.tree.see(selected)
            self.load_slip(selected)
        else:
            self.current = None
            self.loading = True
            for var in self.fields.values():
                var.set("")
            self.note.configure(state="normal")
            self.note.delete("1.0", "end")
            self.note.edit_modified(False)
            self.note.configure(state="disabled")
            self.loading = False
            self.photo = None
            self.preview.configure(image="", text=f"ไม่มีรายการรอลง Excel\nบันทึกแล้ว {len(self.history_rows)} รายการ\nดูได้ที่ ‘ประวัติใน Excel’ หรือกดเปิด Excel")
            self.warning.configure(text="เพิ่มสลิปได้ที่หน้าหลัก รายการที่บันทึกแล้วจะอยู่ใน Excel และหน้าประวัติ")

    def update_count(self):
        self.count_label.configure(text=f"รอลง Excel  {len(self.slips)} รายการ")
        self.export_button.configure(text=f"บันทึกรายการที่รอ ({len(self.slips)})", state="normal" if self.slips and not self.busy else "disabled")
        self.saved_count.configure(text="อ่านรายงานไม่ได้ — ดูรายละเอียดในหน้าประวัติ" if self.history_error else f"บันทึกใน Excel แล้ว  {len(self.history_rows):,} รายการ")
        self.pending_count.configure(text=f"ยังรอบันทึก {len(self.slips)} รายการ — กดปุ่ม ‘บันทึกรายการที่รอ’ ด้านล่าง" if self.slips else "ไม่มีรายการค้าง • รายการที่บันทึกแล้วอยู่ในหน้าประวัติ")
        self.tabs.tab(self.review_page, text=f"  รอลง Excel ({len(self.slips)}) / ตรวจแก้  ")
        self.tabs.tab(self.history_page, text=f"  ประวัติใน Excel ({len(self.history_rows)})  ")
        for widget in [*self.field_widgets, self.approve_button, self.approve_all_button]:
            widget.configure(state="disabled" if self.busy or not self.slips else "normal")
        self.note.configure(state="disabled" if self.busy or not self.slips else "normal")

    def on_edit(self, *_):
        if self.loading or not self.current:
            return
        self.dirty = True
        slip = self.slips.get(self.current)
        if slip and slip.reviewed:
            slip.reviewed = False
            self.tree.set(self.current, "state", "รอตรวจ")
            self.tree.item(self.current, tags=())
            self.update_count()

    def note_changed(self, _=None):
        if self.note.edit_modified():
            self.on_edit()
            self.note.edit_modified(False)

    def save_current(self):
        if not self.current or self.current not in self.slips:
            return
        slip = self.slips[self.current]
        if self.dirty:
            for key, var in self.fields.items():
                value = var.get().strip()
                if value != getattr(slip, key) and key not in slip.manual_fields:
                    slip.manual_fields.append(key)
                setattr(slip, key, value)
            note = self.note.get("1.0", "end-1c").strip()
            if note != slip.note and "note" not in slip.manual_fields:
                slip.manual_fields.append("note")
            slip.note = note
            slip.reviewed = False
            save_draft(self.root_path, slip)
            self.dirty = False

    def selection_changed(self, _=None):
        selected = self.tree.selection()
        if not selected or selected[0] == self.current:
            return
        try:
            self.save_current()
            self.load_slip(selected[0])
        except Exception as exc:
            self.show_error(exc)

    def load_slip(self, key):
        self.current = key
        slip = self.slips[key]
        self.loading = True
        for name, var in self.fields.items():
            var.set(getattr(slip, name))
        self.note.configure(state="normal")
        self.note.delete("1.0", "end")
        self.note.insert("1.0", slip.note)
        self.note.edit_modified(False)
        self.note.configure(state="disabled" if self.busy else "normal")
        self.loading = False
        self.dirty = False
        warnings = "\n".join(slip.warnings[:4]) or "ตรวจวันที่ ผู้รับ ผู้ส่ง ยอดเงิน และชื่อไซต์เทียบกับรูป"
        self.warning.configure(text=warnings)
        self.draw_preview()

    def preview_resize(self, _=None):
        if self.preview_job:
            self.after_cancel(self.preview_job)
        self.preview_job = self.after(150, self.draw_preview)

    def draw_preview(self):
        self.preview_job = None
        if not self.current or self.current not in self.slips:
            return
        path = self.root_path / self.slips[self.current].source
        try:
            im = load_image(path)
            im.thumbnail((max(150, self.preview.winfo_width() - 12), max(200, self.preview.winfo_height() - 12)), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(im)
            im.close()
            self.preview.configure(image=self.photo, text="")
        except Exception as exc:
            self.photo = None
            self.preview.configure(image="", text=f"เปิดภาพไม่ได้\n{path.name}\n{str(exc)[:100]}")

    def approve(self):
        if self.busy or not self.current:
            return
        try:
            self.save_current()
            slip = self.slips[self.current]
            slip.validate_partial()
            if slip.amount:
                slip.amount = str(money(slip.amount))
            slip.reviewed = True
            save_draft(self.root_path, slip)
            keys = list(self.slips)
            idx = keys.index(self.current)
            next_key = next((k for k in keys[idx + 1:] + keys[:idx] if not self.slips[k].reviewed), self.current)
            self.refresh(next_key)
            self.status.configure(text="ยืนยันแล้ว กดเพิ่มลง Excel เมื่อพร้อม • Ctrl+Enter ยืนยันรายการ")
        except Exception as exc:
            self.show_error(exc)

    def approve_all(self):
        if self.busy or not self.slips:
            return
        self.save_current()
        valid = []
        for slip in self.slips.values():
            if slip.reviewed:
                continue
            try:
                slip.validate_partial()
                valid.append(slip)
            except ValueError:
                pass
        if not valid:
            messagebox.showinfo("ยังไม่มีรายการพร้อม", "ตรวจรูปแบบวันที่และยอดเงิน หรือเว้นช่องที่ไม่มีข้อมูลไว้ได้", parent=self)
            return
        if not messagebox.askyesno("ยืนยันหลายรายการ", f"ยืนยันว่าตรวจข้อมูลจากรูปแล้วทั้งหมด {len(valid)} รายการ?\n\nข้อมูลครบไม่ได้แปลว่า OCR อ่านถูก โดยเฉพาะชื่อคน ยอดเงิน และลายมือ\nช่องที่ไม่มีข้อมูลเว้นว่างได้", parent=self):
            return
        for slip in valid:
            slip.reviewed = True
            save_draft(self.root_path, slip)
        self.refresh()

    def set_busy(self, busy, can_stop=False):
        self.busy = busy
        for widget in [self.scan_button, self.reread_button, self.workspace_button, self.sites_button, self.export_button, self.approve_button, self.approve_all_button, self.import_files_button, self.import_folder_button, self.home_scan, self.auto_check, self.history_refresh, self.home_report_button, *self.field_widgets]:
            widget.configure(state="disabled" if busy else "normal")
        self.note.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy and can_stop else "disabled")
        self.stop_main.configure(state="normal" if busy and can_stop else "disabled")
        if busy:
            self.progress.pack(side="left", padx=8, before=self.log_button)
            self.stop_main.pack(side="left", before=self.log_button)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.stop_main.pack_forget()
        self.update_count()

    def start_scan(self, reprocess=False, auto_export=False, selections=None):
        if self.busy:
            return
        try:
            self.save_current()
        except Exception as exc:
            self.show_error(exc)
            return
        self.cancel.clear()
        self.auto_export = auto_export
        self.import_summary = ""
        self.scan_notice = ""
        self.set_busy(True, True)
        self.home_message.configure(text="กำลังคัดลอกและอ่านสลิป กรุณารอ…" if selections else "กำลังตรวจรูปใน data…")
        self.status.configure(text="กำลังอ่านใหม่ โดยเก็บช่องที่คุณแก้เองไว้…" if reprocess else "กำลังค้นหาสลิปใน data…")
        root = self.root_path

        def worker():
            try:
                if selections:
                    copied = import_images(root, selections, self.cancel, lambda kind, data: self.events.put((kind, data)))
                    self.events.put(("copied", copied))
                result = scan(root, lambda kind, data: self.events.put((kind, data)), self.cancel, refresh=reprocess)
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("failed", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def stop_scan(self):
        self.cancel.set()
        self.status.configure(text="กำลังหยุดหลังอ่านรูปปัจจุบันเสร็จ ข้อมูลที่อ่านแล้วจะเก็บไว้")

    def watch_tick(self):
        if self.watch.get() and not self.busy and not self.dirty:
            self.start_scan(auto_export=True)
        self.after(15000, self.watch_tick)

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    i, total, name = data
                    self.status.configure(text=f"กำลังอ่าน {i}/{total}  •  {name}")
                elif kind == "copy_progress":
                    i, total, name = data
                    self.status.configure(text=f"กำลังคัดลอกรูป {i}/{total}  •  {name}")
                elif kind == "copied":
                    self.import_summary = f"คัดลอก {data.copied} รูป • รูปเดิม {data.duplicate} • ไฟล์ที่ไม่รองรับ {data.unsupported} • คัดลอกไม่ได้ {len(data.errors)}"
                    for error in data.errors:
                        self.errors.append(error)
                        self.log_error(error)
                elif kind == "error":
                    self.errors.append(data)
                    self.log_error(data)
                elif kind == "draft":
                    # Keep active editor unchanged while OCR runs in background.
                    self.slips[data.file_hash] = data
                    values = (Path(data.source).name, data.amount or "—", "รอตรวจ")
                    if self.tree.exists(data.file_hash):
                        self.tree.item(data.file_hash, values=values)
                    else:
                        self.tree.insert("", "end", iid=data.file_hash, values=values)
                    self.update_count()
                    if not self.current:
                        self.tree.selection_set(data.file_hash)
                        self.load_slip(data.file_hash)
                    elif self.current == data.file_hash:
                        self.load_slip(data.file_hash)
                elif kind == "done":
                    self.scan_notice = f"\nเปิดภาพไม่ได้ {data.errors} รายการ ดูสาเหตุที่ ‘ดูบันทึก’" if data.errors else ""
                    self.save_current()
                    self.slips = {s.file_hash: s for s in pending(self.root_path)}
                    self.refresh()
                    self.set_busy(False)
                    prefix = "หยุดแล้ว" if data.cancelled else "อ่านเสร็จ"
                    incomplete = sum(any(not getattr(s, key) for key in ["date", "amount", "sender", "recipient"]) for s in self.slips.values())
                    summary = f"{prefix} • อ่านใหม่ {data.added + data.updated} • เคยอ่าน/รูปซ้ำ {data.duplicate + data.cached} • เปิดภาพไม่ได้ {data.errors}"
                    self.status.configure(text=summary)
                    if data.cancelled:
                        self.home_message.configure(text=f"หยุดแล้ว มี {len(self.slips)} รายการที่อ่านไว้ กด ‘บันทึกรายการที่รอ’ ได้ หรือเพิ่มรูปต่อภายหลัง")
                    elif self.auto_export and self.slips:
                        self.export()
                    else:
                        self.reload_history()
                        if self.slips:
                            message = f"อ่านเสร็จ {len(self.slips)} รายการ กด ‘บันทึกรายการที่รอ’ ได้เลย (ข้อมูลหลักยังไม่ครบ {incomplete})"
                        elif self.history_rows:
                            message = f"ไม่มีสลิปใหม่ให้เพิ่ม • Excel มี {len(self.history_rows)} รายการแล้ว\nดูข้อมูลได้ที่ ‘เปิด Excel ของฉัน’ หรือ ‘ประวัติใน Excel’"
                        else:
                            message = "ยังไม่มีรูปที่อ่านได้ กด ‘เพิ่มรูปสลิป’ หรือ ‘เพิ่มทั้งโฟลเดอร์’ รองรับ JPG, PNG, WEBP, BMP, TIFF"
                        self.home_message.configure(text=message + ("\n" + self.import_summary if self.import_summary else "") + (f"\nเปิดภาพไม่ได้ {data.errors} รายการ ดูสาเหตุที่ ‘ดูบันทึก’" if data.errors else ""))
                    if data.errors:
                        self.watch.set(False)
                elif kind == "exported":
                    added, skipped = data
                    self.slips = {s.file_hash: s for s in pending(self.root_path)}
                    self.reload_history()
                    self.refresh()
                    self.set_busy(False)
                    self.status.configure(text=f"เพิ่มลง report.xlsx แล้ว {added} รายการ • ข้ามสลิปซ้ำ {skipped} รายการ")
                    self.home_message.configure(text=f"✓ บันทึกสำเร็จ เพิ่ม {added} รายการ • สะสมทั้งหมด {len(self.history_rows)} รายการ\nกด ‘เปิด Excel ของฉัน’ เพื่อดูข้อมูล วันถัดไปเพิ่มรูปใหม่ได้เลย" + ("\n" + self.import_summary if self.import_summary else "") + self.scan_notice)
                    self.tabs.select(self.home)
                elif kind == "failed":
                    self.set_busy(False)
                    self.watch.set(False)
                    self.show_error(data)
        except queue.Empty:
            pass
        except Exception as exc:
            self.set_busy(False)
            self.show_error(exc)
        self.after(120, self.poll)

    def export(self):
        if self.busy:
            return
        try:
            self.save_current()
            ready = list(self.slips.values())
            if not ready:
                messagebox.showinfo("ไม่มีรายการค้าง", f"บันทึกใน Excel แล้ว {len(self.history_rows)} รายการ\nเพิ่มรูปใหม่ได้ที่หน้าหลัก หรือเปิด Excel เพื่อดูรายการเดิม", parent=self)
                return
            # Copy records so editor activity cannot mutate an in-flight export.
            from .model import Slip
            ready = [Slip(**s.to_dict()) for s in ready]
            self.set_busy(True)
            self.status.configure(text=f"กำลังเพิ่ม {len(ready)} รายการลง Excel…")
            self.home_message.configure(text=f"กำลังเพิ่ม {len(ready)} รายการต่อท้าย Excel เดิม…")
            root = self.root_path

            def worker():
                try:
                    self.events.put(("exported", append_report(root, ready, allow_unreviewed=True)))
                except PermissionError:
                    self.events.put(("failed", "บันทึกไม่ได้ กรุณาปิด report.xlsx ใน Excel และตรวจว่าโฟลเดอร์เขียนไฟล์ได้ แล้วกดเพิ่มอีกครั้ง"))
                except Exception as exc:
                    self.events.put(("failed", str(exc)))
            threading.Thread(target=worker, daemon=True).start()
        except Exception as exc:
            self.show_error(exc)

    def show_error(self, exc):
        text = str(exc)
        self.errors.append(text)
        self.log_error(text)
        self.status.configure(text="ทำรายการไม่สำเร็จ ข้อมูลที่บันทึกก่อนหน้านี้ยังอยู่")
        self.home_message.configure(text=f"ยังทำรายการไม่สำเร็จ: {text}\nข้อมูลที่อ่านไว้ยังอยู่ใน ‘รอลง Excel’ แก้สาเหตุแล้วกดบันทึกอีกครั้ง")
        messagebox.showerror("กรุณาตรวจสอบ", text, parent=self)

    def log_error(self, text):
        try:
            from datetime import datetime
            with (self.root_path / ".slipreport" / "errors.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{datetime.now().isoformat()}  {text}\n")
        except OSError:
            pass

    def text_window(self, title, text):
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("760x560")
        area = tk.Text(window, wrap="word", padx=15, pady=15, font=("Tahoma", 11))
        scroll = ttk.Scrollbar(window, command=area.yview)
        area.configure(yscrollcommand=scroll.set)
        area.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        area.insert("1.0", text)
        area.configure(state="disabled")

    def show_log(self):
        self.text_window("บันทึกปัญหาในการอ่าน", "\n\n".join(self.errors) or "ยังไม่มีข้อผิดพลาดในการทำงานรอบนี้")

    def show_ocr(self):
        if self.current:
            slip = self.slips[self.current]
            self.text_window("ข้อความที่ OCR อ่านได้", f"ไฟล์: {slip.source}\nเลขอ้างอิง: {slip.reference or '—'}\n\n{slip.raw_text or 'ไม่พบข้อความ'}")

    def open_path(self, path):
        try:
            os.startfile(str(path))
        except OSError as exc:
            self.show_error(exc)

    def open_source(self):
        if self.current:
            self.open_path(self.root_path / self.slips[self.current].source)

    def open_report(self):
        if self.busy:
            messagebox.showinfo("กำลังบันทึกข้อมูล", "กรุณารอให้โปรแกรมทำงานเสร็จก่อนเปิด Excel", parent=self)
            return
        path = self.root_path / "report.xlsx"
        if path.exists():
            self.open_path(path)
        else:
            messagebox.showinfo("ยังไม่มี Excel", "ไฟล์ report.xlsx จะถูกสร้างเมื่อเพิ่มรายการครั้งแรก", parent=self)

    def edit_sites(self):
        if self.busy:
            return
        self.save_current()
        window = tk.Toplevel(self)
        window.title("ชื่อไซต์และคำย่อ")
        window.geometry("700x470")
        window.transient(self)
        window.grab_set()
        ttk.Label(window, text="หนึ่งไซต์ต่อหนึ่งบรรทัด: ชื่อไซต์ = คำย่อ, คำอื่นที่พบในสลิป\nตัวอย่าง: บ้านลาดพร้าว = ลาดพร้าว, LP, บ้านคุณเอ\nเว้นว่างคำย่อได้ โปรแกรมจะค้นจากชื่อไซต์ด้วย", padding=16).pack(anchor="w")
        area = tk.Text(window, wrap="word", font=("Tahoma", 11), padx=12, pady=10)
        area.pack(fill="both", expand=True, padx=16)
        for rule in read_sites(self.root_path):
            area.insert("end", rule["name"] + " = " + ", ".join(rule.get("aliases", [])) + "\n")

        def save():
            rules, names = [], set()
            for line in area.get("1.0", "end").splitlines():
                if not line.strip():
                    continue
                name, _, alias = line.partition("=")
                name = name.strip()
                if not name or name in names:
                    messagebox.showerror("ชื่อไซต์ไม่ถูกต้อง", "กรุณาระบุชื่อไซต์และไม่ใช้ชื่อซ้ำ", parent=window)
                    return
                names.add(name)
                rules.append({"name": name, "aliases": [a.strip() for a in alias.split(",") if a.strip()]})
            try:
                save_sites(self.root_path, rules)
                self.site_combo["values"] = [r["name"] for r in rules]
                for slip in self.slips.values():
                    if not slip.reviewed and not slip.site:
                        slip.site, _ = detect_site(slip.raw_text, rules)
                        save_draft(self.root_path, slip)
                self.refresh()
                window.destroy()
            except Exception as exc:
                messagebox.showerror("บันทึกไม่ได้", str(exc), parent=window)
        ttk.Button(window, text="บันทึกชื่อไซต์", style="Accent.TButton", command=save).pack(anchor="e", padx=16, pady=16)

    def close(self):
        if self.busy:
            self.cancel.set()
            messagebox.showinfo("กำลังทำงาน", "โปรแกรมจะหยุดหลังรูปปัจจุบัน กรุณารอให้เสร็จแล้วปิดอีกครั้ง", parent=self)
            return
        try:
            self.save_current()
        except Exception as exc:
            self.show_error(exc)
            return
        if self.lock:
            self.lock.release()
        self.destroy()
