#!/usr/bin/env python3
"""ZIP 文本归档的桌面界面。"""

import multiprocessing
from paste_restore import BackgroundJob, capture_worker, collection_status, restore_worker

if __name__ == "__main__":
    multiprocessing.freeze_support()

import calendar
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from git_archive import branches_for, changed_files, commits_for, repository_for
from zip_to_txt import DEFAULT_PART_SIZE_MB, MAX_PART_SIZE_MB, decrypt_path, encrypt_path, inspect_files


BG, WHITE, INK, MUTED, BLUE, LINE = "#f3f5f8", "#ffffff", "#1d2939", "#667085", "#2563eb", "#dce2eb"


def setup_style(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(size=11)
    family = default_font.actual("family")
    root.configure(background=BG)
    style.configure(".", font=default_font, foreground=INK)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=WHITE, bordercolor=LINE, borderwidth=1, relief="solid")
    style.configure("Plain.TFrame", background=WHITE)
    style.configure("TLabel", background=BG)
    style.configure("Card.TLabel", background=WHITE)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("CardMuted.TLabel", background=WHITE, foreground=MUTED)
    style.configure("Title.TLabel", font=(family, 20, "bold"))
    style.configure("Section.TLabel", background=WHITE, font=(family, 13, "bold"))
    style.configure("Stat.TLabel", background=WHITE, foreground=BLUE, font=(family, 18, "bold"))
    style.configure("TButton", padding=(10, 7), background=WHITE, bordercolor=LINE)
    style.configure("Compact.TButton", padding=(8, 4))
    style.map("Compact.TButton", background=[("selected", "#e8f0ff"), ("active", "#eef4ff")],
              foreground=[("selected", BLUE)])
    style.map("TButton", background=[("active", "#eef4ff")])
    style.configure("Primary.TButton", background=BLUE, foreground=WHITE, bordercolor=BLUE, padding=(18, 9))
    style.map("Primary.TButton", background=[("disabled", "#bdd0f5"), ("active", "#1d4ed8")],
              foreground=[("disabled", "#f8faff"), ("!disabled", WHITE)])
    style.configure("Nav.TButton", anchor="w", borderwidth=0, relief="flat", padding=(13, 13))
    style.map("Nav.TButton", background=[("selected", "#eef4ff"), ("active", "#f5f8fc")],
              foreground=[("disabled", MUTED), ("selected", BLUE)])
    style.configure("TEntry", padding=(8, 7), fieldbackground=WHITE, bordercolor="#cdd5df")
    style.configure("Compact.TEntry", padding=(7, 4))
    style.configure("TCombobox", padding=(7, 6))
    style.configure("Treeview", background=WHITE, fieldbackground=WHITE, rowheight=30, bordercolor=LINE)
    style.configure("Treeview.Heading", background="#f4f6fa", foreground=MUTED, padding=(8, 7))
    style.map("Treeview", background=[("selected", "#e8f0ff")], foreground=[("selected", INK)])
    style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#e9edf4", borderwidth=0)


def size_window(window, width, height, minimum=(860, 560)):
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    width, height = min(width, screen_w - 60), min(height, screen_h - 100)
    window.minsize(min(minimum[0], width), min(minimum[1], height))
    window.geometry(f"{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//2)}")


def preferences_path():
    if sys.platform == "win32":
        parent = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ZipToTxt"
    elif sys.platform == "darwin":
        parent = Path.home() / "Library" / "Application Support" / "ZipToTxt"
    else:
        parent = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "zip-to-txt"
    return parent / "settings.json"


def load_preferences(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        result = {}
        for key in ("source_dir", "output_dir"):
            value = data.get(key)
            if isinstance(value, str) and value and Path(value).is_dir():
                result[key] = value
        value = data.get("part_size_mb")
        if type(value) is int and 1 <= value <= MAX_PART_SIZE_MB:
            result["part_size_mb"] = value
        return result
    except (OSError, ValueError):
        return {}


def save_preferences(path, data):
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        settings = {key: data[key] for key in ("source_dir", "output_dir", "part_size_mb") if key in data}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except OSError:
        pass  # 无法保存设置不影响本次归档；下次仍可使用默认设置。
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def post_latest(updates, value):
    try:
        updates.put_nowait(value)
    except queue.Full:
        try:
            updates.get_nowait()
        except queue.Empty:
            pass
        try:
            updates.put_nowait(value)
        except queue.Full:
            pass


def date_shortcut(label, today=None):
    today = today or date.today()
    if label == "不限":
        return "", ""
    first = last = today
    if label == "昨天":
        first = last = today - timedelta(days=1)
    elif label == "最近 7 天":
        first = today - timedelta(days=6)
    elif label == "本月":
        first = today.replace(day=1)
        last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    elif label != "当天":
        raise ValueError("未知日期快捷项")
    return first.isoformat(), last.isoformat()


def format_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1000


def preview_files(source, commits):
    files, missing = inspect_files(source, changed_files(source, commits))
    sizes = {name: (source / name).stat().st_size for name in files}
    return files, missing, sizes


def show_details(parent, title, content):
    """长文本用可选择的只读窗口查看，表格保留正常字号。"""
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent.winfo_toplevel())
    size_window(window, 820, 380, (580, 260))
    frame = ttk.Frame(window, padding=16)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    text = tk.Text(frame, wrap="word", background=WHITE, foreground=INK,
                   font="TkDefaultFont", padx=12, pady=12, borderwidth=1, relief="solid")
    text.grid(row=0, column=0, sticky="nsew")
    text.insert("1.0", content)
    text.configure(state="disabled")
    scroll = ttk.Scrollbar(frame, command=text.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scroll.set)
    actions = ttk.Frame(frame)
    actions.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
    def copy():
        window.clipboard_clear()
        window.clipboard_append(content)
    ttk.Button(actions, text="复制全文", command=copy).pack(side="left", padx=(0, 8))
    ttk.Button(actions, text="关闭", command=window.destroy).pack(side="left")
    window.bind("<Escape>", lambda _event: window.destroy())
    return window


class CalendarPopup:
    def __init__(self, parent, variable):
        self.variable = variable
        try:
            day = date.fromisoformat(variable.get())
        except ValueError:
            day = date.today()
        self.year, self.month = day.year, day.month
        self.window = tk.Toplevel(parent)
        self.window.title("选择日期")
        self.window.transient(parent.winfo_toplevel())
        self.window.overrideredirect(True)
        self.window.resizable(False, False)
        self.window.geometry(f"+{parent.winfo_rootx()}+{parent.winfo_rooty() + parent.winfo_height()}")
        frame = ttk.Frame(self.window, padding=10)
        frame.pack()
        header = ttk.Frame(frame)
        header.pack(fill="x")
        self.previous = ttk.Button(header, text="‹", width=3, command=lambda: self.move(-1))
        self.previous.pack(side="left")
        self.title = tk.StringVar()
        ttk.Label(header, textvariable=self.title, anchor="center", width=18).pack(side="left")
        self.next = ttk.Button(header, text="›", width=3, command=lambda: self.move(1))
        self.next.pack(side="left")
        self.days = ttk.Frame(frame)
        self.days.pack(pady=8)
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        ttk.Button(actions, text="今天", command=lambda: self.choose(date.today())).pack(side="left", fill="x", expand=True)
        ttk.Button(actions, text="关闭", command=self.window.destroy).pack(side="right")
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self.render()

    def move(self, delta):
        year, month = divmod(self.year * 12 + self.month - 1 + delta, 12)
        if 1 <= year <= 9999:
            self.year, self.month = year, month + 1
            self.render()

    def render(self):
        for child in self.days.winfo_children():
            child.destroy()
        self.title.set(f"{self.year} 年 {self.month} 月")
        self.previous.configure(state="disabled" if (self.year, self.month) == (1, 1) else "normal")
        self.next.configure(state="disabled" if (self.year, self.month) == (9999, 12) else "normal")
        self.day_buttons = {}
        for column, label in enumerate("一二三四五六日"):
            ttk.Label(self.days, text=label, anchor="center").grid(row=0, column=column)
        for row, week in enumerate(calendar.monthcalendar(self.year, self.month), 1):
            for column, number in enumerate(week):
                if number:
                    day = date(self.year, self.month, number)
                    button = ttk.Button(self.days, text=str(number), width=3,
                                        command=lambda day=day: self.choose(day))
                    button.grid(row=row, column=column, padx=1, pady=1)
                    self.day_buttons[number] = button

    def choose(self, day):
        self.variable.set(day.isoformat())
        self.window.destroy()


class DateField(ttk.Frame):
    def __init__(self, parent, variable):
        super().__init__(parent)
        self.variable, self.popup = variable, None
        self.entry = ttk.Entry(self, textvariable=variable, width=12)
        self.entry.pack(side="left")
        self.button = ttk.Button(self, text="日历", command=self.open, width=5)
        self.button.pack(side="left", padx=(3, 0))
        self.entry.bind("<Button-1>", lambda _event: self.open())
        self.entry.bind("<Escape>", lambda _event: self.close_popup())

    def open(self):
        if str(self.entry.cget("state")) == "disabled":
            return
        if self.popup and self.popup.window.winfo_exists():
            self.close_popup()
        else:
            self.popup = CalendarPopup(self, self.variable)

    def close_popup(self):
        if self.popup and self.popup.window.winfo_exists():
            self.popup.window.destroy()

    def set_enabled(self, enabled):
        for control in (self.entry, self.button):
            control.configure(state="normal" if enabled else "disabled")
        if not enabled:
            self.close_popup()


class FileList(ttk.Frame):
    """提交选择和变更确认共用的只读文件清单。"""
    def __init__(self, parent, *, compact=False):
        super().__init__(parent, style="Plain.TFrame")
        self.files, self.missing, self.total_size, self.summary = [], [], 0, ""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        stats = ttk.Frame(self, style="Plain.TFrame")
        stats.grid(row=0, column=0, sticky="ew", pady=(0, 6 if compact else 16))
        self.count, self.size, self.absent = tk.StringVar(value="0"), tk.StringVar(value="0 B"), tk.StringVar(value="0")
        self.summary_text = tk.StringVar()
        if compact:
            ttk.Label(stats, textvariable=self.summary_text, style="Card.TLabel", foreground=BLUE).pack(anchor="w")
        else:
            for column, (variable, label) in enumerate(((self.count, "可打包文件"), (self.size, "未压缩总大小"),
                                                       (self.absent, "缺失，跳过"))):
                stats.columnconfigure(column, weight=1)
                group = ttk.Frame(stats, style="Plain.TFrame", padding=(8, 3))
                group.grid(row=0, column=column, sticky="ew")
                ttk.Label(group, textvariable=variable, style="Stat.TLabel").pack(anchor="w")
                ttk.Label(group, text=label, style="CardMuted.TLabel").pack(anchor="w")
        table = ttk.Frame(self, style="Plain.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("path", "size", "status"), show="headings", height=4 if compact else 10)
        font = tkfont.nametofont("TkDefaultFont")
        for name, label, width in (("path", "相对路径", 200), ("size", "大小", font.measure("999.99 MB") + 24),
                                   ("status", "状态", font.measure("缺失，跳过") + 24)):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=80, stretch=False)
        self.tree.tag_configure("missing", foreground="#a8681c")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind("<Configure>", self.fit_columns)
        self.tree.bind("<Double-1>", self.show_details)
        self.tree.bind("<Return>", self.show_details)

    def show_details(self, event=None):
        row = self.tree.identify_row(event.y) if event and event.keysym != "Return" else self.tree.focus()
        if row:
            path, size, status = self.tree.item(row, "values")
            return show_details(self, "文件详情", f"相对路径：\n{path}\n\n大小：{size}\n状态：{status}")

    def fit_columns(self, event):
        fixed = self.tree.column("size", "width") + self.tree.column("status", "width")
        self.tree.column("path", width=max(80, event.width - fixed - 4))

    def show(self, files, missing, sizes=None):
        sizes = sizes or {}
        self.files, self.missing = list(files), list(missing)
        self.total_size = sum(sizes.get(name, 0) for name in files)
        self.summary = (f"可打包 {len(files)} 个文件；未压缩合计 {format_size(self.total_size)}；"
                        f"已不存在 {len(missing)} 个（跳过）")
        self.summary_text.set(self.summary)
        self.count.set(str(len(files)))
        self.size.set(format_size(self.total_size))
        self.absent.set(str(len(missing)))
        self.tree.delete(*self.tree.get_children())
        for name in files:
            self.tree.insert("", "end", values=(name, format_size(sizes[name]) if name in sizes else "—", "可打包"))
        for name in missing:
            self.tree.insert("", "end", values=(name, "—", "缺失，跳过"), tags=("missing",))


class GitPicker:
    def __init__(self, parent: tk.Tk, source: Path, on_accept, state=None) -> None:
        state = state or {}
        self.source, self.on_accept = source, on_accept
        self.window = tk.Toplevel(parent)
        self.window.title("选择提交，预览文件")
        size_window(self.window, 1240, 900, (980, 600))
        self.window.transient(parent)
        self.window.grab_set()
        self.branch = tk.StringVar(value=state.get("branch", ""))
        self.branch_keyword = tk.StringVar(value=state.get("keyword", ""))
        self.start_date = tk.StringVar(value=state.get("start", ""))
        self.end_date = tk.StringVar(value=state.get("end", ""))
        self.author = tk.StringVar(value=state.get("author", ""))
        self.recent_hours = state.get("recent_hours", 0)
        if self.recent_hours not in (0, 24, 48):
            self.recent_hours = 0
        self.changing_dates = False
        self.active_shortcut = f"近 {self.recent_hours // 24} 天" if self.recent_hours else ""
        self.time_range = tk.StringVar()
        self.status, self.count = tk.StringVar(), tk.StringVar(value="已选 0 个提交")
        self.branch_count, self.preview_status = tk.StringVar(), tk.StringVar(value="请勾选提交")
        self.branches, self.visible = {}, {}
        self.selected = dict(state.get("selected", {}))
        self.loaded_branch, self.rows, self.shown = "", [], 200
        self.loading, self.query_ok, self.closed = False, False, False
        self.preview_generation, self.preview_after = 0, None
        self.preview_job_active, self.preview_ok = False, False
        self.fit_after = None
        self.preview_data, self.preview_commits = None, ()
        self.events = queue.Queue()

        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text="选择提交，预览文件", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=f"{source}  ·  仅包含此目录及子目录", style="Muted.TLabel",
                  wraplength=1100).grid(row=1, column=0, sticky="w", pady=(4, 8))
        filter_card = ttk.Frame(frame, padding=(12, 8), style="Card.TFrame")
        filter_card.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        filter_card.columnconfigure(0, weight=1)
        branches = ttk.Frame(filter_card, style="Plain.TFrame")
        branches.grid(row=0, column=0, sticky="ew")
        branches.columnconfigure(1, weight=1)
        branches.columnconfigure(3, weight=3)
        branches.columnconfigure(5, weight=2)
        ttk.Label(branches, text="分支关键字", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.keyword_entry = ttk.Entry(branches, textvariable=self.branch_keyword, width=10, style="Compact.TEntry")
        self.keyword_entry.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(branches, text="分支", style="Card.TLabel").grid(row=0, column=2, padx=(10, 6))
        self.branch_box = ttk.Combobox(branches, textvariable=self.branch, state="readonly", width=18)
        self.branch_box.grid(row=0, column=3, sticky="ew")
        self.branch_box.bind("<<ComboboxSelected>>", self.change_branch)
        self.keyword_trace = self.branch_keyword.trace_add("write", self.filter_branches)
        ttk.Label(branches, text="用户", style="Card.TLabel").grid(row=0, column=4, padx=(10, 6))
        self.author_entry = ttk.Entry(branches, textvariable=self.author, width=14, style="Compact.TEntry")
        self.author_entry.grid(row=0, column=5, sticky="ew")
        self.author_entry.bind("<Return>", lambda _event: self.load())
        self.filter_button = ttk.Button(branches, text="应用筛选", command=self.load, style="Compact.TButton")
        self.filter_button.grid(row=0, column=6, padx=(10, 0))
        dates = ttk.Frame(filter_card, style="Plain.TFrame")
        dates.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        manual_dates = ttk.Frame(dates, style="Plain.TFrame")
        manual_dates.grid(row=0, column=0, sticky="w")
        ttk.Label(manual_dates, text="日期", style="CardMuted.TLabel").pack(side="left", padx=(0, 8))
        self.start_field, self.end_field = DateField(manual_dates, self.start_date), DateField(manual_dates, self.end_date)
        self.start_field.pack(side="left")
        ttk.Label(manual_dates, text="至", style="CardMuted.TLabel").pack(side="left", padx=6)
        self.end_field.pack(side="left")
        for field in (self.start_field, self.end_field):
            field.configure(style="Plain.TFrame")
            field.entry.configure(style="Compact.TEntry", width=10)
            field.button.configure(style="Compact.TButton", width=3)
            field.entry.bind("<Return>", lambda _event: self.load())
        shortcuts = ttk.Frame(dates, style="Plain.TFrame")
        shortcuts.grid(row=0, column=1, sticky="w", padx=(12, 0))
        def arrange_dates(event):
            stacked = event.width < manual_dates.winfo_reqwidth() + shortcuts.winfo_reqwidth() + 12
            shortcuts.grid(row=1 if stacked else 0, column=0 if stacked else 1, sticky="w",
                           padx=(0 if stacked else 12, 0), pady=(6 if stacked else 0, 0))
        dates.bind("<Configure>", arrange_dates)
        self.shortcut_buttons = []
        for label in ("当天", "昨天", "近 1 天", "近 2 天", "最近 7 天", "本月", "不限"):
            button = ttk.Button(shortcuts, text=label, width=0, style="Compact.TButton",
                                command=lambda label=label: self.quick_date(label))
            button.pack(side="left", padx=(0, 4))
            self.shortcut_buttons.append(button)
        self.update_time_filter()
        self.date_traces = [(variable, variable.trace_add("write", self.manual_dates_changed))
                            for variable in (self.start_date, self.end_date)]

        self.panes = ttk.Panedwindow(frame, orient="vertical")
        self.panes.grid(row=3, column=0, sticky="nsew")
        top = ttk.Frame(self.panes, padding=(12, 8), style="Card.TFrame")
        bottom = ttk.Frame(self.panes, padding=(12, 8), style="Card.TFrame")
        self.panes.add(top, weight=1)
        self.panes.add(bottom, weight=1)
        self.commit_pane, self.files_pane = top, bottom
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        heading = ttk.Frame(top, style="Plain.TFrame")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(heading, text="提交记录", style="Section.TLabel").pack(side="left")
        ttk.Label(heading, textvariable=self.branch_count, style="CardMuted.TLabel").pack(side="left", padx=12)
        ttk.Label(heading, textvariable=self.time_range, style="CardMuted.TLabel").pack(side="left")
        ttk.Label(heading, text="双击行查看完整信息；拖动分隔线调整上下高度", style="CardMuted.TLabel").pack(side="right")
        table = ttk.Frame(top, style="Plain.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("pick", "sha", "time", "author", "subject"),
                                 show="headings", selectmode="browse", height=4)
        font = tkfont.nametofont("TkDefaultFont")
        for name, title, width in (("pick", "选择", font.measure("选择") + 24),
                ("sha", "提交", font.measure("88888888") + 24),
                ("time", "提交时间", font.measure("2026-12-31 23:59") + 24),
                ("author", "用户", font.measure("developer <user@example.com>") + 24),
                ("subject", "说明", 400)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=200 if name == "subject" else width, stretch=name == "subject",
                             anchor="center" if name == "pick" else "w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind("<Button-1>", self.click_checkbox)
        self.tree.bind("<space>", self.space_checkbox)
        self.tree.bind("<Double-1>", self.show_commit_details)
        self.tree.bind("<Return>", self.show_commit_details)
        actions = ttk.Frame(top, style="Plain.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.all_button = ttk.Button(actions, text="全选筛选结果", command=self.select_all, style="Compact.TButton")
        self.invert_button = ttk.Button(actions, text="反选", command=self.invert, style="Compact.TButton")
        self.clear_button = ttk.Button(actions, text="清空全部", command=self.clear, style="Compact.TButton")
        self.more_button = ttk.Button(actions, text="加载更多", command=self.more, style="Compact.TButton")
        for button in (self.all_button, self.invert_button, self.clear_button, self.more_button):
            button.pack(side="left", padx=(0, 4))
        ttk.Label(actions, textvariable=self.status, style="CardMuted.TLabel").pack(side="right")

        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)
        header = ttk.Frame(bottom, style="Plain.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(header, text="打包文件清单", style="Section.TLabel").pack(side="left")
        self.refresh_button = ttk.Button(header, text="刷新", command=self.request_preview, style="Compact.TButton")
        self.refresh_button.pack(side="right")
        self.file_list = FileList(bottom, compact=True)
        self.file_list.grid(row=1, column=0, sticky="nsew")
        for widget in (self.panes, self.tree, self.file_list.tree):
            widget.bind("<Configure>", self.queue_fit_panes, add="+")
        self.preview_help = ttk.Label(header, text="当前磁盘内容 · 双击查看完整路径",
                                      style="CardMuted.TLabel", wraplength=400)
        self.preview_help.pack(side="left", padx=12)
        self.preview_message = ttk.Label(header, textvariable=self.preview_status, style="CardMuted.TLabel", wraplength=600)
        self.preview_message.pack(side="right", padx=10)
        bottom.bind("<Configure>", self.wrap_preview)
        footer = ttk.Frame(frame)
        footer.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(footer, textvariable=self.count).pack(side="left")
        self.accept_button = ttk.Button(footer, text="使用此文件清单", style="Primary.TButton",
                                       command=self.accept, state="disabled")
        self.accept_button.pack(side="right")
        ttk.Button(footer, text="返回", command=self.close).pack(side="right", padx=(0, 8))
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = self.window.after(80, self.poll)
        self.load(initial=True)

    def wrap_preview(self, event):
        for label in (self.preview_help, self.preview_message):
            label.configure(wraplength=max(180, event.width - 32))

    def queue_fit_panes(self, _event):
        if not self.closed and self.fit_after is None:
            self.fit_after = self.window.after_idle(self.keep_rows_visible)

    def keep_rows_visible(self):
        self.fit_after = None
        if self.closed:
            return
        row_height = int(ttk.Style(self.window).lookup("Treeview", "rowheight"))
        needed = []
        for pane, tree in ((self.commit_pane, self.tree), (self.files_pane, self.file_list.tree)):
            one_row = tree.winfo_reqheight() - (int(tree.cget("height")) - 1) * row_height
            needed.append(pane.winfo_reqheight() - tree.winfo_reqheight() + one_row)
        total = self.panes.winfo_height()
        gap = total - self.commit_pane.winfo_height() - self.files_pane.winfo_height()
        if sum(needed) > total - gap:
            width, height = self.window.minsize()
            minimum = self.window.winfo_height() + sum(needed) + gap - total
            if minimum <= self.window.winfo_screenheight() - 100:
                self.window.minsize(width, max(height, minimum))
            return
        if sum(needed) <= total - gap:
            current = self.panes.sashpos(0)
            position = max(needed[0], min(current, total - gap - needed[1]))
            if position != current:
                self.panes.sashpos(0, position)

    def filter_branches(self, *_args):
        needle = self.branch_keyword.get().strip().casefold()
        labels = [label for label in self.branches if needle in label.split("：", 1)[-1].casefold()]
        self.branch_box.configure(values=labels)
        self.branch_count.set(f"{len(labels)} 个候选" if labels else "无匹配分支")

    def update_time_filter(self):
        self.time_range.set(f"最近 {self.recent_hours} 小时（滚动）" if self.recent_hours else "日期按本机时区、包含全天")
        for button in self.shortcut_buttons:
            button.state(["selected"] if button.cget("text") == self.active_shortcut else ["!selected"])

    def manual_dates_changed(self, *_args):
        if not self.changing_dates:
            self.recent_hours, self.active_shortcut = 0, ""
            self.update_time_filter()

    def quick_date(self, label):
        if not self.loading:
            self.recent_hours = {"近 1 天": 24, "近 2 天": 48}.get(label, 0)
            first, last = ("", "") if self.recent_hours else date_shortcut(label)
            self.changing_dates = True
            try:
                self.start_date.set(first)
                self.end_date.set(last)
            finally:
                self.changing_dates = False
            self.active_shortcut = label
            self.update_time_filter()
            self.load()

    def show_commit_details(self, event=None):
        if event and event.keysym != "Return" and self.tree.identify_column(event.x) == "#1":
            return "break"
        sha = self.tree.identify_row(event.y) if event and event.keysym != "Return" else self.tree.focus()
        if sha in self.visible:
            item = self.visible[sha]
            timestamp = datetime.fromtimestamp(item.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            return show_details(self.window, "提交详情", f"提交：{item.sha}\n提交时间（本机时区）：{timestamp}\n"
                                f"用户：{item.author}\n邮箱：{item.email}\n\n说明：\n{item.subject}")

    def update_actions(self):
        ready = not self.loading and self.query_ok
        for button in (self.all_button, self.invert_button):
            button.configure(state="normal" if ready else "disabled")
        self.more_button.configure(state="normal" if ready and len(self.visible) < len(self.rows) else "disabled")
        self.refresh_button.configure(state="normal" if ready and self.selected else "disabled")
        self.accept_button.configure(state="normal" if ready and self.preview_ok else "disabled")

    def set_loading(self, loading):
        self.loading = loading
        state = "disabled" if loading else "normal"
        for control in (self.keyword_entry, self.author_entry, self.filter_button, self.clear_button,
                        *self.shortcut_buttons):
            control.configure(state=state)
        self.branch_box.configure(state="disabled" if loading else "readonly")
        self.start_field.set_enabled(not loading)
        self.end_field.set_enabled(not loading)
        self.tree.state(["disabled"] if loading or not self.query_ok else ["!disabled"])
        self.update_actions()

    def load(self, initial=False):
        if self.loading:
            return
        label, branches = self.branch.get(), dict(self.branches)
        start, end, author = self.start_date.get(), self.end_date.get(), self.author.get()
        recent_hours = self.recent_hours
        source, events = self.source, self.events
        self.set_loading(True)
        self.status.set("正在读取本地提交…")
        def work():
            try:
                if initial:
                    choices, current = branches_for(source)
                    selected_label = label if label in choices else current
                else:
                    choices, selected_label = branches, label
                if selected_label not in choices:
                    raise ValueError("请选择有效分支")
                rows = commits_for(source, choices[selected_label], start, end, author, recent_hours=recent_hours)
                events.put(("rows", (choices, selected_label, rows)))
            except Exception as exc:
                events.put(("query-error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "rows":
                    self.branches, label, self.rows = value
                    if self.branch.get() and self.branch.get() != label:
                        self.selected.clear()
                    self.branch.set(label)
                    self.loaded_branch = label
                    self.filter_branches()
                    self.shown, self.query_ok = 200, True
                    self.render()
                    self.set_loading(False)
                    self.request_preview()
                elif event == "query-error":
                    self.query_ok = False
                    self.set_loading(False)
                    self.status.set("查询失败，请修改筛选后重试")
                    messagebox.showerror("无法读取提交", str(value), parent=self.window)
                else:
                    self.preview_job_active = False
                    generation = value[0]
                    if generation == self.preview_generation:
                        if event == "files":
                            _, commits, files, missing, sizes = value
                            self.preview_data, self.preview_commits = (files, missing, sizes), commits
                            self.file_list.show(files, missing, sizes)
                            self.preview_ok = bool(files)
                            self.preview_status.set("清单已更新" if files else "没有可打包文件；缺失项已列出")
                        else:
                            self.preview_ok, self.preview_data = False, None
                            self.file_list.show([], [])
                            self.preview_status.set(f"无法读取文件：{value[1]}")
                        self.update_actions()
                    elif self.selected and self.preview_after is None:
                        self.start_preview()
        except queue.Empty:
            self.poll_id = self.window.after(80, self.poll)

    def render(self):
        self.tree.delete(*self.tree.get_children())
        self.visible = {item.sha: item for item in self.rows[:self.shown]}
        for item in self.visible.values():
            self.tree.insert("", "end", iid=item.sha, values=("☑" if item.sha in self.selected else "☐",
                             item.sha[:8], datetime.fromtimestamp(item.timestamp).strftime("%Y-%m-%d %H:%M"),
                             f"{item.author} <{item.email}>", item.subject))
        self.status.set(f"显示 {len(self.visible)} / {len(self.rows)} 个提交")
        self.update_checks()

    def update_checks(self):
        for sha in self.visible:
            self.tree.set(sha, "pick", "☑" if sha in self.selected else "☐")
        hidden = len(set(self.selected) - set(self.visible))
        self.count.set(f"已选 {len(self.selected)} 个提交" + (f"（列表外 {hidden} 个）" if hidden else ""))
        self.update_actions()

    def toggle(self, sha):
        if self.loading or not self.query_ok or sha not in self.visible:
            return
        if sha in self.selected:
            self.selected.pop(sha)
        else:
            self.selected[sha] = self.visible[sha]
        self.update_checks()
        self.request_preview()

    def click_checkbox(self, event):
        if self.tree.identify_column(event.x) == "#1":
            sha = self.tree.identify_row(event.y)
            if sha:
                self.tree.focus(sha)
                self.toggle(sha)
                return "break"

    def space_checkbox(self, _event):
        self.toggle(self.tree.focus())
        return "break"

    def select_all(self):
        if not self.loading and self.query_ok:
            self.selected.update((item.sha, item) for item in self.rows)
            self.update_checks()
            self.request_preview()

    def invert(self):
        if not self.loading and self.query_ok:
            for item in self.rows:
                if item.sha in self.selected:
                    self.selected.pop(item.sha)
                else:
                    self.selected[item.sha] = item
            self.update_checks()
            self.request_preview()

    def clear(self):
        self.selected.clear()
        self.update_checks()
        self.request_preview()

    def change_branch(self, _event=None):
        if self.branch.get() != self.loaded_branch:
            self.clear()
            self.load()

    def more(self):
        if not self.loading and self.query_ok:
            self.shown += 200
            self.render()

    def request_preview(self):
        self.preview_generation += 1
        self.preview_ok, self.preview_data = False, None
        if self.preview_after is not None:
            self.window.after_cancel(self.preview_after)
            self.preview_after = None
        self.update_actions()
        if not self.selected:
            self.file_list.show([], [])
            self.preview_status.set("请勾选提交")
            return
        self.preview_status.set("正在更新文件清单…")
        self.preview_after = self.window.after(180, self.start_preview)

    def start_preview(self):
        self.preview_after = None
        if self.closed or not self.selected or self.preview_job_active:
            return
        generation, commits = self.preview_generation, tuple(self.selected)
        source, events = self.source, self.events
        self.preview_job_active = True
        def work():
            try:
                files, missing, sizes = preview_files(source, commits)
                events.put(("files", (generation, commits, files, missing, sizes)))
            except Exception as exc:
                events.put(("files-error", (generation, str(exc))))
        threading.Thread(target=work, daemon=True).start()

    def accept(self):
        if self.loading or not self.query_ok or not self.preview_ok or self.preview_data is None:
            return
        files, missing, sizes = self.preview_data
        state = {"branch": self.branch.get(), "keyword": self.branch_keyword.get(),
                 "start": self.start_date.get(), "end": self.end_date.get(), "author": self.author.get(),
                 "recent_hours": self.recent_hours,
                 "selected": dict(self.selected), "files": list(files), "missing": list(missing), "sizes": dict(sizes)}
        commits = self.preview_commits
        self.close()
        self.on_accept(commits, state)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.branch_keyword.trace_remove("write", self.keyword_trace)
        for variable, trace in self.date_traces:
            variable.trace_remove("write", trace)
        self.window.after_cancel(self.poll_id)
        if self.preview_after is not None:
            self.window.after_cancel(self.preview_after)
        if self.fit_after is not None:
            self.window.after_cancel(self.fit_after)
        self.start_field.close_popup()
        self.end_field.close_popup()
        self.window.destroy()


class FilePreview:
    def __init__(self, parent, source, files, missing, on_accept, on_cancel, *,
                 sizes=None, accept_text="开始加密打包", cancel_text="取消"):
        self.files, self.missing = files, missing
        self.window = tk.Toplevel(parent)
        self.window.title("确认最新文件清单")
        size_window(self.window, 850, 560, (740, 440))
        self.window.transient(parent)
        self.window.grab_set()
        self.on_accept, self.on_cancel = on_accept, on_cancel
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="核对打包文件清单", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"所选目录：{source}", style="Muted.TLabel", wraplength=800).pack(
            anchor="w", pady=(6, 14))
        self.list = FileList(frame)
        self.list.pack(fill="both", expand=True)
        self.list.show(files, missing, sizes)
        self.tree, self.summary, self.total_size = self.list.tree, self.list.summary, self.list.total_size
        ttk.Label(frame, text="文件清单或缺失状态已变化。内容取打包时当前磁盘版本。",
                  style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=(12, 0))
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(14, 0))
        self.accept_button = ttk.Button(actions, text=accept_text, command=self.accept,
                                       style="Primary.TButton", state="normal" if files else "disabled")
        self.accept_button.pack(side="right")
        ttk.Button(actions, text=cancel_text, command=self.cancel).pack(side="right", padx=8)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)

    def accept(self):
        if self.files:
            self.window.destroy()
            self.on_accept()

    def cancel(self):
        self.window.destroy()
        self.on_cancel()


class PasteParts(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, style="Card.TFrame", padding=18)
        self.app, self.cards, self.counter = app, [], 0
        self.temporary, self.job, self.active_card = None, None, None
        self.columnconfigure(0, weight=1)
        actions = ttk.Frame(self, style="Plain.TFrame")
        actions.grid(row=0, column=0, sticky="ew")
        ttk.Label(actions, text="粘贴文本分片", style="Section.TLabel").pack(side="left")
        self.add_button = ttk.Button(actions, text="＋ 添加分片", command=self.add)
        self.add_button.pack(side="right")
        self.clear_button = ttk.Button(actions, text="清空全部", command=self.clear)
        self.clear_button.pack(side="right", padx=8)
        ttk.Label(self, text="每项粘贴一份 TXT 的完整内容，可乱序；空格和换行会自动移除。",
                  style="CardMuted.TLabel", wraplength=620).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.summary = tk.StringVar()
        self.summary_label = ttk.Label(self, textvariable=self.summary, style="Card.TLabel", wraplength=620)
        self.summary_label.grid(row=2, column=0, sticky="w", pady=(8, 12))
        self.body = ttk.Frame(self, style="Plain.TFrame")
        self.body.grid(row=3, column=0, sticky="ew")
        self.body.columnconfigure(0, weight=1)
        self.add()

    @property
    def loading(self):
        return self.job is not None

    def parts(self):
        return [card["info"] for card in self.cards if card["info"] is not None]

    @property
    def ready(self):
        return not self.loading and not any(card["error"] for card in self.cards) and collection_status(self.parts())[0]

    def add(self):
        if self.loading or self.app.busy:
            return
        self.counter += 1
        card = dict(number=self.counter, info=None, error="", generation=0)
        frame = card["frame"] = ttk.Frame(self.body, style="Card.TFrame", padding=12)
        frame.grid(row=len(self.cards), column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        card["title"] = tk.StringVar(value=f"输入项 {self.counter} · 尚未粘贴")
        card["preview"] = tk.StringVar(value="选中这里按 Ctrl+V / ⌘V，或点击右侧“粘贴”")
        card["status"] = tk.StringVar(value="只显示短预览，完整分片在后台处理")
        ttk.Label(frame, textvariable=card["title"], style="Card.TLabel").grid(row=0, column=0, sticky="w")
        entry = card["entry"] = ttk.Entry(frame, textvariable=card["preview"], state="readonly", width=1)
        entry.grid(row=1, column=0, sticky="ew", pady=(7, 6))
        entry.bind("<<Paste>>", lambda _event: self.paste(card) or "break")
        buttons = ttk.Frame(frame, style="Plain.TFrame")
        buttons.grid(row=1, column=1, padx=(8, 0))
        card["paste"] = ttk.Button(buttons, text="粘贴", command=lambda: self.paste(card))
        card["paste"].pack(side="left")
        card["delete"] = ttk.Button(buttons, text="删除", command=lambda: self.delete(card))
        card["delete"].pack(side="left", padx=(6, 0))
        card["status_label"] = ttk.Label(frame, textvariable=card["status"], style="CardMuted.TLabel", wraplength=540)
        card["status_label"].grid(row=2, column=0, columnspan=2, sticky="w")
        self.cards.append(card)
        self.refresh()

    def delete(self, card):
        if self.loading or self.app.busy:
            return
        if card["info"]:
            Path(card["info"]["path"]).unlink(missing_ok=True)
        card["frame"].destroy()
        self.cards.remove(card)
        for row, remaining in enumerate(self.cards):
            remaining["frame"].grid(row=row)
        if not self.cards:
            self.add()
        self.refresh()

    def clear(self):
        if self.loading or self.app.busy:
            return
        for card in self.cards:
            card["frame"].destroy()
        self.cards.clear()
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
        self.add()

    def paste(self, card):
        if self.loading or self.app.busy:
            return
        self.app.reset_result()
        self.app.status.set("正在读取和校验粘贴分片…")
        self.app.progress.configure(mode="indeterminate")
        self.app.progress.start(12)
        self.app.progress_text.set("—")
        self.app.byte_progress.set("正在读取剪贴板，完成后显示分片大小")
        try:
            if self.temporary is None:
                self.temporary = tempfile.TemporaryDirectory(prefix=".ztxt-paste-")
            card["generation"] += 1
            destination = Path(self.temporary.name) / f"input-{card['number']}-{card['generation']}.txt"
            self.job = BackgroundJob(capture_worker, str(destination))
            self.active_card = card
            card["status"].set("正在读取剪贴板并检查分片…")
            card["status_label"].configure(foreground=MUTED)
        except Exception as exc:
            card["error"] = str(exc)
            card["status"].set(card["error"])
            self.app.progress.stop()
            self.app.status.set("分片检查失败")
        self.refresh()

    def refresh(self):
        ready, description = collection_status(self.parts())
        errors = any(card["error"] for card in self.cards)
        if self.loading:
            description = "正在读取和校验粘贴内容，界面仍可响应…"
        elif errors:
            description = "有输入项未通过检查，请替换或删除对应项"
        self.summary.set(description)
        self.summary_label.configure(foreground="#16724b" if ready and not errors and not self.loading else MUTED)
        self.set_enabled(not self.app.busy)
        if hasattr(self.app, "run_button"):
            self.app.refresh_paste_run()

    def set_enabled(self, enabled):
        state = "normal" if enabled and not self.loading else "disabled"
        for control in (self.add_button, self.clear_button):
            control.configure(state=state)
        for card in self.cards:
            card["paste"].configure(state=state)
            card["delete"].configure(state=state)

    def poll(self):
        if self.job is None:
            return
        for event, value in self.job.poll():
            card = self.active_card
            if event == "bytes":
                card["status"].set(f"已读取 {format_size(value)}，正在后台检查…")
                self.app.byte_progress.set(f"已读取 {format_size(value)}，正在后台检查…")
            elif event in ("done", "error"):
                if event == "done":
                    if card["info"]:
                        Path(card["info"]["path"]).unlink(missing_ok=True)
                    card["info"], card["error"] = value, ""
                    card["title"].set(f"输入项 {card['number']} · 第 {value['index']} / {value['count']} 片 · {format_size(value['size'])}")
                    card["preview"].set(value["preview"] + "…")
                    card["status"].set("分片初步检查通过；还原时验证密码和数据完整性")
                    card["status_label"].configure(foreground="#16724b")
                    card["paste"].configure(text="替换")
                else:
                    card["error"] = value
                    card["status"].set(value + ("；原分片保留" if card["info"] else ""))
                    card["status_label"].configure(foreground="#b42318")
                self.job, self.active_card = None, None
                self.app.progress.stop()
                self.app.progress.configure(mode="determinate", value=0)
                self.app.progress_text.set("0%")
                self.app.status.set("分片检查通过" if event == "done" else "分片检查失败")
                self.app.byte_progress.set("收齐分片后可开始还原")
                self.refresh()

    def close(self):
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None


class App:
    def __init__(self, root: tk.Tk, *, config_path=None):
        self.root, self.config_path = root, Path(config_path) if config_path is not None else preferences_path()
        setup_style(root)
        root.title("ZIP 文本归档")
        size_window(root, 1060, 760)
        self.preferences = load_preferences(self.config_path)
        self.mode, self.path = tk.StringVar(value="encrypt"), tk.StringVar()
        self.output_dir = tk.StringVar(value=self.preferences.get("output_dir", ""))
        self.part_size_mb = tk.StringVar(value=str(self.preferences.get("part_size_mb", DEFAULT_PART_SIZE_MB)))
        self.password, self.confirmation = tk.StringVar(), tk.StringVar()
        self.status, self.output = tk.StringVar(value="准备就绪"), tk.StringVar()
        self.heading, self.source_label, self.output_hint = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.progress_text, self.byte_progress = tk.StringVar(value="0%"), tk.StringVar(value="处理时显示已处理／总大小")
        self.current_file = tk.StringVar()
        self.events, self.progress_events = queue.Queue(), queue.Queue(maxsize=1)
        self.busy, self.password_visible = False, False
        self.git_commits, self.git_selection_state, self.git_snapshot = (), {}, None
        self.git_ready, self.git_generation, self.git_after = False, 0, None
        self.git_status = tk.StringVar(value="选择 Git 仓库中的目录可启用提交打包")
        self.pending_task, self.form_stacked = None, None
        self.paste_job = None

        shell = ttk.Frame(root)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(shell, style="Plain.TFrame", padding=(16, 24))
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="ZIP 文本归档", style="Section.TLabel").pack(anchor="w", pady=(0, 26))
        self.encrypt_choice = ttk.Button(sidebar, text="压缩并加密", style="Nav.TButton",
                                         command=lambda: self.set_mode("encrypt"))
        self.decrypt_choice = ttk.Button(sidebar, text="解密并还原", style="Nav.TButton",
                                         command=lambda: self.set_mode("decrypt"))
        self.paste_choice = ttk.Button(sidebar, text="粘贴文本还原", style="Nav.TButton",
                                      command=lambda: self.set_mode("paste"))
        self.encrypt_choice.pack(fill="x", pady=(0, 8))
        self.decrypt_choice.pack(fill="x")
        self.paste_choice.pack(fill="x", pady=(8, 0))
        ttk.Label(sidebar, text="安全打包与还原\nv0.5.0", style="CardMuted.TLabel").pack(side="bottom", anchor="w")
        main = ttk.Frame(shell, padding=(24, 22))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        ttk.Label(main, textvariable=self.heading, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.subheading = tk.StringVar(value="路径与参数分区展示，处理结果显示在下方。")
        ttk.Label(main, textvariable=self.subheading, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(5, 18))

        form = ttk.Frame(main)
        form.grid(row=2, column=0, sticky="nsew")
        form.columnconfigure(0, weight=1)
        form.rowconfigure(0, weight=1)
        self.form_canvas = tk.Canvas(form, background=BG, highlightthickness=0, width=1, height=1)
        self.form_canvas.grid(row=0, column=0, sticky="nsew")
        self.form_scrollbar = ttk.Scrollbar(form, orient="vertical", command=self.form_canvas.yview)
        self.form_scrollbar.grid(row=0, column=1, sticky="ns")
        self.form_canvas.configure(yscrollcommand=self.form_scrollbar.set)
        self.form_body = ttk.Frame(self.form_canvas)
        self.form_window = self.form_canvas.create_window(0, 0, window=self.form_body, anchor="nw")
        self.paste_parts = PasteParts(self.form_body, self)
        self.path_card = ttk.Frame(self.form_body, style="Card.TFrame", padding=18)
        self.options_card = ttk.Frame(self.form_body, style="Card.TFrame", padding=18)
        self.path_card.columnconfigure(0, weight=1)
        self.options_card.columnconfigure(0, weight=1)

        ttk.Label(self.path_card, text="源路径", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self.path_card, textvariable=self.source_label, style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(12, 5))
        self.path_entry = ttk.Entry(self.path_card, textvariable=self.path, width=1)
        self.path_entry.grid(row=2, column=0, sticky="ew")
        browse = ttk.Frame(self.path_card, style="Plain.TFrame")
        browse.grid(row=3, column=0, sticky="e", pady=(6, 0))
        self.file_button = ttk.Button(browse, text="选择文件", command=self.browse_file)
        self.folder_button = ttk.Button(browse, text="选择目录", command=self.browse_folder)
        self.file_button.pack(side="left", padx=(0, 6))
        self.folder_button.pack(side="left")
        self.path_help = ttk.Label(self.path_card, text="可选择路径，也可直接粘贴完整路径。",
                                   style="CardMuted.TLabel", wraplength=320)
        self.path_help.grid(row=4, column=0, sticky="w", pady=(7, 0))
        self.git_frame = ttk.Frame(self.path_card, style="Plain.TFrame")
        self.git_frame.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.git_frame.columnconfigure(0, weight=1)
        self.git_button = ttk.Button(self.git_frame, text="按 Git 提交选择", command=self.choose_commits, state="disabled")
        self.git_button.grid(row=0, column=0, sticky="w")
        self.complete_button = ttk.Button(self.git_frame, text="完整目录", command=self.clear_commits, state="disabled")
        self.complete_button.grid(row=0, column=1, padx=(6, 0), sticky="e")
        self.git_description = ttk.Label(self.git_frame, textvariable=self.git_status,
                                         style="CardMuted.TLabel", wraplength=320)
        self.git_description.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.source_widgets = list(self.path_card.winfo_children())
        ttk.Label(self.path_card, text="输出位置", style="Section.TLabel").grid(
            row=6, column=0, sticky="w", pady=(16, 10))
        self.output_label = ttk.Label(self.path_card, text="输出目录（可选）", style="Card.TLabel")
        self.output_label.grid(row=7, column=0, sticky="w")
        destination = ttk.Frame(self.path_card, style="Plain.TFrame")
        destination.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        destination.columnconfigure(0, weight=1)
        self.output_dir_entry = ttk.Entry(destination, textvariable=self.output_dir, width=1)
        self.output_dir_entry.grid(row=0, column=0, sticky="ew")
        self.output_dir_button = ttk.Button(destination, text="选择目录", command=self.browse_output_dir)
        self.output_dir_button.grid(row=0, column=1, padx=(6, 0))
        self.output_description = ttk.Label(self.path_card, textvariable=self.output_hint,
                                             style="CardMuted.TLabel", wraplength=320)
        self.output_description.grid(row=9, column=0, sticky="w", pady=(7, 0))

        self.options_heading = ttk.Label(self.options_card, text="分片与密码", style="Section.TLabel")
        self.options_heading.grid(row=0, column=0, sticky="w", pady=(0, 16))
        self.size_frame = ttk.Frame(self.options_card, style="Plain.TFrame")
        self.size_frame.grid(row=1, column=0, sticky="ew", pady=(0, 18))
        self.size_label = ttk.Label(self.size_frame, text="每个 TXT 文件最大", style="Card.TLabel")
        self.size_label.grid(row=0, column=0, columnspan=3, sticky="w")
        self.size_entry = ttk.Entry(self.size_frame, textvariable=self.part_size_mb, width=7)
        self.size_entry.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(self.size_frame, text="MB", style="CardMuted.TLabel").grid(row=1, column=1, padx=7, pady=(6, 0))
        ttk.Label(self.size_frame, text=f"1～{MAX_PART_SIZE_MB}，默认 50", style="CardMuted.TLabel").grid(
            row=1, column=2, pady=(6, 0))
        ttk.Label(self.options_card, text="密码", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        password_row = ttk.Frame(self.options_card, style="Plain.TFrame")
        password_row.grid(row=3, column=0, sticky="ew", pady=(6, 16))
        password_row.columnconfigure(0, weight=1)
        self.password_entry = ttk.Entry(password_row, textvariable=self.password, show="*", width=1)
        self.password_entry.grid(row=0, column=0, sticky="ew")
        self.show_button = ttk.Button(password_row, text="显示", command=self.toggle_password, width=5)
        self.show_button.grid(row=0, column=1, padx=(6, 0))
        self.confirm_label = ttk.Label(self.options_card, text="确认密码", style="Card.TLabel")
        self.confirm_label.grid(row=4, column=0, sticky="w")
        self.confirm_entry = ttk.Entry(self.options_card, textvariable=self.confirmation, show="*", width=1)
        self.confirm_entry.grid(row=5, column=0, sticky="ew", pady=(6, 12))
        self.settings_hint = ttk.Label(self.options_card, text="记住目录和分片大小。\n密码仅用于本次处理。",
                                       style="CardMuted.TLabel", wraplength=280)
        self.settings_hint.grid(row=6, column=0, sticky="w")

        self.form_canvas.bind("<Configure>", self.resize_form)
        self.form_body.bind("<Configure>", lambda _event: self.update_scroll_region())
        root.bind("<MouseWheel>", self.scroll_form)
        root.bind("<Button-4>", self.scroll_form)
        root.bind("<Button-5>", self.scroll_form)
        actions = ttk.Frame(main)
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 12))
        ttk.Label(actions, text="一次处理一项，完成后可继续使用。", style="Muted.TLabel").pack(side="left")
        self.run_button = ttk.Button(actions, text="压缩并加密", command=self.start, style="Primary.TButton")
        self.run_button.pack(side="right")

        state = ttk.Frame(main, style="Card.TFrame", padding=(16, 12))
        state.grid(row=4, column=0, sticky="ew")
        state.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(state, textvariable=self.status, style="Card.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")
        ttk.Label(state, textvariable=self.progress_text, style="Card.TLabel", foreground=BLUE).grid(
            row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(state, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(9, 6))
        ttk.Label(state, textvariable=self.byte_progress, style="CardMuted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w")
        self.file_progress_label = ttk.Label(state, textvariable=self.current_file,
                                             style="CardMuted.TLabel", wraplength=700)
        self.file_progress_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.file_progress_label.grid_remove()
        self.file_trace = self.current_file.trace_add("write", lambda *_args: self.file_progress_label.grid()
                                                     if self.current_file.get() else self.file_progress_label.grid_remove())
        self.result_frame = ttk.Frame(state, style="Plain.TFrame")
        self.result_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.result_frame.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(self.result_frame, textvariable=self.output, state="readonly", width=1)
        self.output_entry.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.open_button = ttk.Button(self.result_frame, text="打开输出目录", command=self.open_output, state="disabled")
        self.copy_button = ttk.Button(self.result_frame, text="复制路径", command=self.copy_output, state="disabled")
        self.open_button.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.copy_button.grid(row=1, column=1, sticky="e", pady=(8, 0))
        self.result_frame.grid_remove()
        self.controls = [self.encrypt_choice, self.decrypt_choice, self.paste_choice, self.path_entry, self.file_button,
                         self.folder_button, self.output_dir_entry, self.output_dir_button, self.size_entry,
                         self.password_entry, self.confirm_entry, self.show_button, self.run_button,
                         self.git_button, self.complete_button]
        self.path_trace = self.path.trace_add("write", self.path_changed)
        self.output_trace = self.output_dir.trace_add("write", lambda *_args: self.update_output_hint())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.update_mode()
        self.path_entry.focus_set()
        self.poll_id = root.after(80, self.poll)

    def resize_form(self, event=None):
        width = event.width if event is not None else self.form_canvas.winfo_width()
        stacked = width < 700
        pasting = self.mode.get() == "paste"
        if stacked != self.form_stacked:
            self.form_stacked = stacked
            self.form_body.columnconfigure(0, weight=1)
            self.form_body.columnconfigure(1, weight=0 if stacked else 1)
            self.path_card.grid(row=1 if pasting else 0, column=0, sticky="nsew", padx=(0, 0 if stacked else 16),
                                pady=(0, 16 if stacked else 0))
            self.options_card.grid(row=(2 if stacked else 1) if pasting else (1 if stacked else 0),
                                   column=0 if stacked else 1, sticky="nsew")
        self.form_canvas.itemconfigure(self.form_window, width=width)
        wrap = max(180, (width if stacked else width // 2) - 52)
        for label in (self.path_help, self.git_description, self.output_description):
            label.configure(wraplength=wrap)
        self.file_progress_label.configure(wraplength=max(220, width - 32))
        self.paste_parts.summary_label.configure(wraplength=max(220, width - 48))
        for card in self.paste_parts.cards:
            card["status_label"].configure(wraplength=max(220, width - 80))
        self.update_scroll_region()

    def update_scroll_region(self):
        self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))

    def scroll_form(self, event):
        if self.form_body.winfo_reqheight() > self.form_canvas.winfo_height():
            amount = (-1 if event.num == 4 else 1) if event.num in (4, 5) else (-1 if event.delta > 0 else 1)
            self.form_canvas.yview_scroll(amount, "units")
            return "break"

    def set_mode(self, mode):
        if not self.busy and self.mode.get() != mode:
            self.mode.set(mode)
            self.path.set("")
            self.password.set("")
            self.confirmation.set("")
            self.hide_password()
            self.update_mode()

    def update_mode(self):
        encrypting = self.mode.get() == "encrypt"
        pasting = self.mode.get() == "paste"
        self.heading.set("粘贴文本还原" if pasting else ("新建归档包" if encrypting else "还原归档包"))
        self.subheading.set("粘贴完整 TXT 分片，收齐后输入密码还原。" if pasting else "路径与参数分区展示，处理结果显示在下方。")
        self.source_label.set("要打包的文件或目录" if encrypting else "TXT 分片所在目录")
        self.encrypt_choice.state(["selected"] if encrypting else ["!selected"])
        self.decrypt_choice.state(["selected"] if self.mode.get() == "decrypt" else ["!selected"])
        self.paste_choice.state(["selected"] if pasting else ["!selected"])
        for control in self.source_widgets:
            control.grid_remove() if pasting else control.grid()
        self.paste_parts.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16)) if pasting else self.paste_parts.grid_remove()
        self.output_label.configure(text="输出目录（必选）" if pasting else "输出目录（可选）")
        self.options_heading.configure(text="分片与密码" if encrypting else "解密密码")
        self.file_button.configure(state="normal" if encrypting else "disabled")
        self.run_button.configure(text="压缩并加密" if encrypting else "解密并还原")
        for control in (self.git_frame, self.size_frame, self.confirm_label, self.confirm_entry):
            control.grid() if encrypting else control.grid_remove()
        self.path_changed()
        self.update_output_hint()
        self.form_stacked = None
        self.resize_form()
        self.refresh_paste_run()

    def refresh_paste_run(self):
        enabled = not self.busy and not self.paste_parts.loading
        if self.mode.get() == "paste":
            enabled = enabled and self.paste_parts.ready
        self.run_button.configure(state="normal" if enabled else "disabled")

    def update_output_hint(self):
        path = self.path.get().strip()
        name = Path(path).name + ".txtparts" if path and self.mode.get() == "encrypt" else (
            "文件名.txtparts" if self.mode.get() == "encrypt" else "restored")
        self.output_hint.set(f"将在此位置新建 {name}。\n留空使用默认位置，已有目录不覆盖。")
        if self.mode.get() == "paste":
            self.output_hint.set("请选择输出位置，将在其中新建 restored。\n已有目录不覆盖。")

    def path_changed(self, *_args):
        self.git_generation += 1
        self.git_commits, self.git_selection_state, self.git_snapshot = (), {}, None
        self.git_ready = False
        self.git_button.configure(state="disabled", text="按 Git 提交选择")
        self.complete_button.configure(state="disabled")
        if self.git_after is not None:
            self.root.after_cancel(self.git_after)
            self.git_after = None
        if not self.busy:
            self.reset_result()
        if self.mode.get() == "encrypt":
            self.git_status.set("正在识别 Git 仓库…" if self.path.get().strip() else "选择仓库中的目录可按提交打包")
            if self.path.get().strip():
                self.git_after = self.root.after(300, self.detect_git)
        self.update_output_hint()

    def detect_git(self):
        self.git_after = None
        generation, path = self.git_generation, self.path.get().strip()
        events = self.events
        def work():
            try:
                repository_for(path)
                result = (True, "Git 可用 · 当前为完整目录打包")
            except (OSError, ValueError) as exc:
                result = (False, f"Git 不可用：{exc}")
            events.put(("git-detected", (generation, *result)))
        threading.Thread(target=work, daemon=True).start()

    def choose_commits(self):
        if self.git_ready and not self.busy:
            self.picker = GitPicker(self.root, Path(self.path.get().strip()).expanduser(),
                                    self.use_commits, self.git_selection_state)

    def use_commits(self, commits, state=None):
        self.git_commits, self.git_selection_state = commits, state or {}
        self.git_snapshot = ((list(state["files"]), list(state["missing"])) if state and "files" in state else None)
        self.git_button.configure(text="修改 Git 提交")
        count = len(state.get("files", [])) if state else 0
        self.git_status.set(f"已选 {len(commits)} 个提交 · {count} 个文件\n仅限所选目录及其子目录")
        self.complete_button.configure(state="normal")

    def clear_commits(self):
        self.git_commits, self.git_selection_state, self.git_snapshot = (), {}, None
        self.git_button.configure(text="按 Git 提交选择")
        self.complete_button.configure(state="disabled")
        self.git_status.set("当前为完整目录打包")

    def toggle_password(self):
        self.password_visible = not self.password_visible
        for entry in (self.password_entry, self.confirm_entry):
            entry.configure(show="" if self.password_visible else "*")
        self.show_button.configure(text="隐藏" if self.password_visible else "显示")

    def hide_password(self):
        self.password_visible = False
        self.password_entry.configure(show="*")
        self.confirm_entry.configure(show="*")
        self.show_button.configure(text="显示")

    def remember_settings(self):
        data = dict(self.preferences)
        path = Path(self.path.get().strip()).expanduser()
        try:
            if self.path.get().strip() and (path.is_dir() or path.is_file()):
                data["source_dir"] = str(path if path.is_dir() else path.parent)
            output = self.output_dir.get().strip()
            if not output or Path(output).expanduser().is_dir():
                data["output_dir"] = str(Path(output).expanduser()) if output else ""
            value = int(self.part_size_mb.get())
            if 1 <= value <= MAX_PART_SIZE_MB:
                data["part_size_mb"] = value
        except (OSError, ValueError):
            pass
        self.preferences = data
        save_preferences(self.config_path, data)

    def browse_file(self):
        selected = filedialog.askopenfilename(title="选择要压缩的文件", parent=self.root,
                                              initialdir=self.preferences.get("source_dir"))
        if selected:
            self.path.set(selected)
            self.remember_settings()

    def browse_folder(self):
        title = "选择要压缩的目录" if self.mode.get() == "encrypt" else "选择 TXT 分片所在目录"
        selected = filedialog.askdirectory(title=title, parent=self.root,
                                           initialdir=self.preferences.get("source_dir"))
        if selected:
            self.path.set(selected)
            self.remember_settings()

    def browse_output_dir(self):
        selected = filedialog.askdirectory(title="选择输出位置（将在其中新建目录）", parent=self.root,
                                           initialdir=self.output_dir.get().strip() or self.preferences.get("source_dir"))
        if selected:
            self.output_dir.set(selected)
            self.remember_settings()

    def reset_result(self):
        self.output.set("")
        self.result_frame.grid_remove()
        self.open_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.status.set("准备就绪")
        self.status_label.configure(foreground=INK)
        self.progress.configure(value=0)
        self.progress_text.set("0%")
        self.byte_progress.set("处理时显示已处理／总大小")
        self.current_file.set("")

    def start(self):
        if self.busy or self.paste_parts.loading:
            return
        path, output_dir, password = self.path.get().strip(), self.output_dir.get().strip() or None, self.password.get()
        pasting = self.mode.get() == "paste"
        if pasting and (not self.paste_parts.ready or not output_dir or not password):
            messagebox.showerror("无法开始", "请收齐一套分片、选择输出目录并输入密码", parent=self.root)
            return
        if not pasting and (not path or not password):
            messagebox.showerror("无法开始", "请选择路径并输入密码", parent=self.root)
            return
        encrypting = self.mode.get() == "encrypt"
        if encrypting and password != self.confirmation.get():
            messagebox.showerror("无法开始", "两次输入的密码不一致", parent=self.root)
            return
        options = {"output_dir": output_dir}
        if encrypting:
            try:
                size_mb = int(self.part_size_mb.get())
                if not 1 <= size_mb <= MAX_PART_SIZE_MB:
                    raise ValueError
            except ValueError:
                messagebox.showerror("无法开始", f"每个 TXT 上限必须是 1 到 {MAX_PART_SIZE_MB} 之间的整数 MB", parent=self.root)
                return
            options["part_size_mb"] = size_mb
        self.remember_settings()
        self.reset_result()
        self.busy = True
        for control in self.controls:
            control.configure(state="disabled")
        self.paste_parts.set_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.progress_text.set("—")
        self.status.set("正在准备…")
        if pasting:
            try:
                self.paste_job = BackgroundJob(restore_worker, self.paste_parts.parts(), output_dir, password)
            except Exception as exc:
                self.events.put(("error", str(exc)))
        elif encrypting and self.git_commits:
            self.pending_task = (path, password, options)
            commits = self.git_commits
            events = self.events
            def preview():
                try:
                    files, missing, sizes = preview_files(Path(path).expanduser(), commits)
                    events.put(("preview", (files, missing, sizes)))
                except Exception as exc:
                    events.put(("error", str(exc)))
            self.status.set("正在检查文件清单…")
            threading.Thread(target=preview, daemon=True).start()
        else:
            self.run_archive(encrypt_path if encrypting else decrypt_path, path, password, options)

    def show_progress(self, value):
        self.status.set(value.phase)
        self.current_file.set(value.filename if len(value.filename) < 100 else "…" + value.filename[-100:])
        if value.total is None:
            if str(self.progress.cget("mode")) != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
            self.progress_text.set("—")
            self.byte_progress.set("正在扫描，获取总量…")
        else:
            self.progress.stop()
            percent = 100 if value.finished else min(99, 100 * value.processed / value.total if value.total else 0)
            self.progress.configure(mode="determinate", value=percent)
            self.progress_text.set(f"{percent:.0f}%")
            self.byte_progress.set(f"已处理 {format_size(value.processed)} / {format_size(value.total)}")

    def run_archive(self, operation, path, password, options):
        events, updates = self.events, self.progress_events
        def work():
            try:
                result = operation(path, password, on_progress=lambda value: post_latest(updates, value), **options)
                events.put(("done", result))
            except Exception as exc:
                events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def accept_preview(self, files, missing=(), sizes=None):
        path, password, options = self.pending_task
        self.git_snapshot = (list(files), list(missing))
        self.git_selection_state.update(files=list(files), missing=list(missing), sizes=sizes or {})
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.run_archive(encrypt_path, path, password, {**options, "files": files})

    def cancel_preview(self):
        self.finish_task()
        self.password.set("")
        self.confirmation.set("")
        self.progress.configure(value=0)
        self.progress_text.set("0%")
        self.byte_progress.set("")
        self.status.set("已取消打包")

    def finish_task(self):
        self.busy, self.pending_task = False, None
        self.progress.stop()
        self.progress.configure(mode="determinate")
        while not self.progress_events.empty():
            try:
                self.progress_events.get_nowait()
            except queue.Empty:
                break
        for control in self.controls:
            control.configure(state="normal")
        self.file_button.configure(state="normal" if self.mode.get() == "encrypt" else "disabled")
        self.git_button.configure(state="normal" if self.git_ready else "disabled")
        self.complete_button.configure(state="normal" if self.git_commits else "disabled")
        self.hide_password()
        self.paste_parts.refresh()

    def poll(self):
        self.paste_parts.poll()
        if self.paste_job is not None:
            for event, value in self.paste_job.poll():
                if event == "progress":
                    post_latest(self.progress_events, value)
                else:
                    self.events.put((event, value))
            if self.paste_job.finished:
                self.paste_job = None
        try:
            value = self.progress_events.get_nowait()
            if self.busy:
                self.show_progress(value)
        except queue.Empty:
            pass
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "git-detected":
                    generation, ready, description = value
                    if generation == self.git_generation:
                        self.git_ready = ready
                        if not self.git_commits:
                            self.git_status.set(description)
                        self.git_button.configure(state="normal" if ready and not self.busy else "disabled")
                    continue
                if event == "preview":
                    files, missing, sizes = value
                    if self.git_snapshot == (files, missing) and files:
                        self.accept_preview(files, missing, sizes)
                    else:
                        self.progress.stop()
                        if not files and self.git_snapshot is None:
                            self.events.put(("error", "所选提交在当前目录内没有可打包文件"))
                        else:
                            self.status.set("文件清单已变化，请确认")
                            self.preview = FilePreview(self.root, self.pending_task[0], files, missing,
                                                       lambda: self.accept_preview(files, missing, sizes), self.cancel_preview,
                                                       sizes=sizes)
                    continue
                self.finish_task()
                self.password.set("")
                self.confirmation.set("")
                if event == "done":
                    if self.mode.get() == "paste":
                        self.paste_parts.clear()
                    self.output.set(str(value))
                    self.status.set("处理完成")
                    self.status_label.configure(foreground="#16724b")
                    self.progress.configure(value=100)
                    self.progress_text.set("100%")
                    self.current_file.set("")
                    self.result_frame.grid()
                    self.open_button.configure(state="normal")
                    self.copy_button.configure(state="normal")
                else:
                    self.status.set("处理失败")
                    self.status_label.configure(foreground="#b42318")
                    self.progress.configure(value=0)
                    self.progress_text.set("—")
                    self.byte_progress.set("任务未完成")
                    self.current_file.set(str(value)[:180])
                    messagebox.showerror("处理失败", str(value), parent=self.root)
        except queue.Empty:
            self.poll_id = self.root.after(80, self.poll)

    def copy_output(self):
        if self.output.get():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.output.get())
            self.current_file.set("输出路径已复制")

    def open_output(self):
        path = self.output.get()
        if not path:
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def close(self):
        if self.busy or self.paste_parts.loading:
            messagebox.showinfo("正在处理", "请等待当前任务完成后关闭窗口", parent=self.root)
        else:
            self.paste_parts.close()
            self.remember_settings()
            self.root.after_cancel(self.poll_id)
            if self.git_after is not None:
                self.root.after_cancel(self.git_after)
            for variable, trace in ((self.path, self.path_trace), (self.output_dir, self.output_trace),
                                     (self.current_file, self.file_trace)):
                variable.trace_remove("write", trace)
            if getattr(self, "picker", None) is not None:
                self.picker.close()
                self.picker = None
            self.preview = None
            self.root.destroy()


def main():
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
