#!/usr/bin/env python3
"""ZIP 文本归档的桌面界面。"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from zip_to_txt import DEFAULT_PART_SIZE_MB, MAX_PART_SIZE_MB, decrypt_path, encrypt_path


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ZIP 文本归档")
        self.root.minsize(560, 400)
        self.mode = tk.StringVar(value="encrypt")
        self.path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.part_size_mb = tk.StringVar(value=str(DEFAULT_PART_SIZE_MB))
        self.password = tk.StringVar()
        self.confirmation = tk.StringVar()
        self.status = tk.StringVar(value="请选择要处理的路径")
        self.output = tk.StringVar()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="ZIP 文本归档", font=("", 17, "bold")).grid(row=0, column=0, sticky="w")
        modes = ttk.Frame(frame)
        modes.grid(row=1, column=0, sticky="w", pady=(16, 12))
        self.encrypt_choice = ttk.Radiobutton(modes, text="压缩并加密", variable=self.mode, value="encrypt", command=self.update_mode)
        self.encrypt_choice.pack(side="left", padx=(0, 20))
        self.decrypt_choice = ttk.Radiobutton(modes, text="解密并解压缩", variable=self.mode, value="decrypt", command=self.update_mode)
        self.decrypt_choice.pack(side="left")

        ttk.Label(frame, text="路径").grid(row=2, column=0, sticky="w")
        picker = ttk.Frame(frame)
        picker.grid(row=3, column=0, sticky="ew", pady=(4, 12))
        picker.columnconfigure(0, weight=1)
        self.path_entry = ttk.Entry(picker, textvariable=self.path)
        self.path_entry.grid(row=0, column=0, sticky="ew")
        self.file_button = ttk.Button(picker, text="选择文件", command=self.browse_file)
        self.file_button.grid(row=0, column=1, padx=(8, 0))
        self.folder_button = ttk.Button(picker, text="选择目录", command=self.browse_folder)
        self.folder_button.grid(row=0, column=2, padx=(8, 0))

        ttk.Label(frame, text="输出位置（可选，留空使用默认位置）").grid(row=4, column=0, sticky="w")
        destination = ttk.Frame(frame)
        destination.grid(row=5, column=0, sticky="ew", pady=(4, 12))
        destination.columnconfigure(0, weight=1)
        self.output_dir_entry = ttk.Entry(destination, textvariable=self.output_dir)
        self.output_dir_entry.grid(row=0, column=0, sticky="ew")
        self.output_dir_button = ttk.Button(destination, text="选择目录", command=self.browse_output_dir)
        self.output_dir_button.grid(row=0, column=1, padx=(8, 0))

        self.size_label = ttk.Label(frame, text=f"每个 TXT 上限（MB，1～{MAX_PART_SIZE_MB}）")
        self.size_label.grid(row=6, column=0, sticky="w")
        self.size_entry = ttk.Entry(frame, textvariable=self.part_size_mb, width=8)
        self.size_entry.grid(row=7, column=0, sticky="w", pady=(4, 12))

        ttk.Label(frame, text="密码").grid(row=8, column=0, sticky="w")
        self.password_entry = ttk.Entry(frame, textvariable=self.password, show="*")
        self.password_entry.grid(row=9, column=0, sticky="ew", pady=(4, 10))
        self.confirm_label = ttk.Label(frame, text="确认密码")
        self.confirm_label.grid(row=10, column=0, sticky="w")
        self.confirm_entry = ttk.Entry(frame, textvariable=self.confirmation, show="*")
        self.confirm_entry.grid(row=11, column=0, sticky="ew", pady=(4, 12))

        actions = ttk.Frame(frame)
        actions.grid(row=12, column=0, sticky="ew")
        self.run_button = ttk.Button(actions, text="开始压缩并加密", command=self.start)
        self.run_button.pack(side="left")
        self.open_button = ttk.Button(actions, text="打开输出目录", command=self.open_output, state="disabled")
        self.open_button.pack(side="left", padx=(10, 0))

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=13, column=0, sticky="ew", pady=(16, 7))
        ttk.Label(frame, textvariable=self.status).grid(row=14, column=0, sticky="w")
        self.output_entry = ttk.Entry(frame, textvariable=self.output, state="readonly")
        self.output_entry.grid(row=15, column=0, sticky="ew", pady=(8, 0))

        self.controls = [self.encrypt_choice, self.decrypt_choice, self.path_entry, self.file_button,
                         self.folder_button, self.output_dir_entry, self.output_dir_button,
                         self.size_entry, self.password_entry, self.confirm_entry, self.run_button]
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.path_entry.focus_set()

    def update_mode(self) -> None:
        encrypting = self.mode.get() == "encrypt"
        self.file_button.configure(state="normal" if encrypting else "disabled")
        self.run_button.configure(text="开始压缩并加密" if encrypting else "开始解密并解压缩")
        if encrypting:
            self.size_label.grid()
            self.size_entry.grid()
            self.confirm_label.grid()
            self.confirm_entry.grid()
        else:
            self.size_label.grid_remove()
            self.size_entry.grid_remove()
            self.confirm_label.grid_remove()
            self.confirm_entry.grid_remove()
        self.output.set("")
        self.open_button.configure(state="disabled")

    def browse_file(self) -> None:
        selected = filedialog.askopenfilename(title="选择要压缩的文件")
        if selected:
            self.path.set(selected)

    def browse_folder(self) -> None:
        title = "选择要压缩的目录" if self.mode.get() == "encrypt" else "选择 TXT 分片所在目录"
        selected = filedialog.askdirectory(title=title)
        if selected:
            self.path.set(selected)

    def browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择输出位置（将在其中新建目录）")
        if selected:
            self.output_dir.set(selected)

    def start(self) -> None:
        path = self.path.get().strip()
        output_dir = self.output_dir.get().strip() or None
        password = self.password.get()
        if not path or not password:
            messagebox.showerror("无法开始", "请选择路径并输入密码")
            return
        if self.mode.get() == "encrypt" and password != self.confirmation.get():
            messagebox.showerror("无法开始", "两次输入的密码不一致")
            return
        encrypting = self.mode.get() == "encrypt"
        if encrypting:
            try:
                size_mb = int(self.part_size_mb.get())
                if not 1 <= size_mb <= MAX_PART_SIZE_MB:
                    raise ValueError
            except ValueError:
                messagebox.showerror("无法开始", f"每个 TXT 上限必须是 1 到 {MAX_PART_SIZE_MB} 之间的整数 MB")
                return

        self.busy = True
        self.output.set("")
        self.open_button.configure(state="disabled")
        for control in self.controls:
            control.configure(state="disabled")
        self.progress.start(12)
        self.status.set("正在准备…")
        operation = encrypt_path if encrypting else decrypt_path
        options = {"output_dir": output_dir}
        if encrypting:
            options["part_size_mb"] = size_mb

        def work() -> None:
            try:
                result = operation(path, password, lambda stage: self.events.put(("stage", stage)),
                                   **options)
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, self.poll)

    def poll(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "stage":
                    self.status.set(str(value))
                    continue
                self.busy = False
                self.progress.stop()
                for control in self.controls:
                    control.configure(state="normal")
                self.file_button.configure(state="normal" if self.mode.get() == "encrypt" else "disabled")
                self.password.set("")
                self.confirmation.set("")
                if event == "done":
                    self.output.set(str(value))
                    self.status.set("处理完成")
                    self.open_button.configure(state="normal")
                    messagebox.showinfo("完成", f"输出目录：\n{value}")
                else:
                    self.status.set("处理失败")
                    messagebox.showerror("处理失败", str(value))
                return
        except queue.Empty:
            self.root.after(100, self.poll)

    def open_output(self) -> None:
        path = self.output.get()
        if not path:
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc))

    def close(self) -> None:
        if self.busy:
            messagebox.showinfo("正在处理", "请等待当前任务完成后关闭窗口")
        else:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
