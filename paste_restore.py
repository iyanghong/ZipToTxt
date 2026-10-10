"""粘贴分片的读取、暂存和还原；大文本只在工作进程中处理。"""

import base64
import binascii
import codecs
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from zip_to_txt import COPY_CHUNK_SIZE, HEADER, MAGIC, MAX_TEXT_LIMIT, decrypt_path


def clipboard_chunks():
    if sys.platform == "darwin":
        with subprocess.Popen(["/usr/bin/pbpaste"], stdout=subprocess.PIPE) as process:
            while chunk := process.stdout.read(COPY_CHUNK_SIZE):
                yield chunk
            if process.wait():
                raise ValueError("无法读取剪贴板文本")
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user, kernel = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
        user.OpenClipboard.argtypes, user.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
        user.GetClipboardData.argtypes, user.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
        kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [wintypes.HGLOBAL], ctypes.c_void_p
        kernel.GlobalSize.argtypes, kernel.GlobalSize.restype = [wintypes.HGLOBAL], ctypes.c_size_t
        kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        for _ in range(20):
            if user.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            raise ValueError("剪贴板正被其他程序占用，请稍后重试")
        handle, pointer = None, None
        try:
            handle = user.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle or not (pointer := kernel.GlobalLock(handle)):
                raise ValueError("剪贴板中没有可读取的文本")
            decoder = codecs.getincrementaldecoder("utf-16-le")()
            length = kernel.GlobalSize(handle)
            for offset in range(0, length, COPY_CHUNK_SIZE * 2):
                text = decoder.decode(ctypes.string_at(pointer + offset, min(COPY_CHUNK_SIZE * 2, length - offset)))
                text, separator, _ = text.partition("\0")
                yield text.encode("utf-8")
                if separator:
                    break
        finally:
            if pointer:
                kernel.GlobalUnlock(handle)
            user.CloseClipboard()
    else:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
            for offset in range(0, len(text), COPY_CHUNK_SIZE):
                yield text[offset:offset + COPY_CHUNK_SIZE].encode("utf-8")
        except tk.TclError as exc:
            raise ValueError("剪贴板中没有可读取的文本") from exc
        finally:
            root.destroy()


def inspect_part(path):
    path = Path(path)
    size = path.stat().st_size
    if not 0 < size <= MAX_TEXT_LIMIT:
        raise ValueError("分片不能为空，去除空白后每片最多 200 MB")
    header = None
    with path.open("rb") as stream:
        block = stream.read(COPY_CHUNK_SIZE)
        preview = block[:96].decode("ascii", errors="replace")
        while block:
            following = stream.read(COPY_CHUNK_SIZE)
            try:
                if following and b"=" in block:
                    raise binascii.Error("分片中间出现 Base64 结束符")
                decoded = base64.b64decode(block, validate=True)
            except binascii.Error as exc:
                raise ValueError("不是有效 Base64；每项只粘贴一份 TXT 的完整内容") from exc
            if header is None:
                if len(decoded) < HEADER.size + 16:
                    raise ValueError("分片过短或内容不完整")
                header = HEADER.unpack(decoded[:HEADER.size])
            block = following
    magic, salt, archive_id, index, count = header
    if magic != MAGIC or not 1 <= index <= count <= 999_999:
        raise ValueError("不是本程序的有效文本分片")
    return dict(path=str(path), size=size, index=index, count=count,
                salt=salt.hex(), archive_id=archive_id.hex(), preview=preview)


def store_part(chunks, destination, progress=None):
    """分块去空白，保留一个末尾 NUL 的既有兼容规则，不保存全文字符串。"""
    destination = Path(destination)
    pending, size, created = b"", 0, False
    try:
        with destination.open("xb") as output:
            created = True
            for chunk in chunks:
                block = pending + chunk.translate(None, b" \t\r\n")
                if not block:
                    continue
                pending, block = block[-1:], block[:-1]
                size += len(block)
                if size > MAX_TEXT_LIMIT:
                    raise ValueError("去除空白后每片最多 200 MB")
                output.write(block)
                if progress:
                    progress(size)
            if pending != b"\0":
                size += len(pending)
                if size > MAX_TEXT_LIMIT:
                    raise ValueError("去除空白后每片最多 200 MB")
                output.write(pending)
        return inspect_part(destination)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def collection_status(parts):
    if not parts:
        return False, "请添加并粘贴文本分片"
    identity = {(part["salt"], part["archive_id"], part["count"]) for part in parts}
    if len(identity) != 1:
        return False, "分片来自不同归档，请删除或替换错误片"
    indices = [part["index"] for part in parts]
    if len(indices) != len(set(indices)):
        return False, "存在重复分片，请删除或替换重复片"
    count = parts[0]["count"]
    if len(indices) != count:
        # 最多展示十个缺失序号，避免恶意片头生成庞大的界面文本。
        missing, received = [], set(indices)
        for index in range(1, count + 1):
            if index not in received:
                missing.append(str(index))
                if len(missing) == 10:
                    break
        suffix = "等" if count - len(indices) > len(missing) else ""
        return False, f"已收到 {len(parts)} / {count} 片，缺少第 {', '.join(missing)} {suffix}片"
    return True, f"已收齐 {count} 片 · 共 {sum(part['size'] for part in parts) / 1_000_000:.2f} MB · 可开始还原"


def capture_worker(destination, connection):
    try:
        last = 0
        def progress(size):
            nonlocal last
            if size - last >= 5_000_000:
                connection.send(("bytes", size))
                last = size
        result = store_part(clipboard_chunks(), destination, progress)
        connection.send(("done", result))
    except Exception as exc:
        connection.send(("error", str(exc)))
    finally:
        connection.close()


def restore_worker(parts, output_dir, password, connection):
    try:
        ready, description = collection_status(parts)
        if not ready:
            raise ValueError(description)
        with tempfile.TemporaryDirectory(prefix=".ztxt-restore-", dir=Path(parts[0]["path"]).parent) as staging:
            for part in parts:
                os.link(part["path"], Path(staging) / f"part-{part['index']:06d}.txt")
            result = decrypt_path(staging, password, output_dir=output_dir,
                                  on_progress=lambda value: connection.send(("progress", value)))
        connection.send(("done", str(result)))
    except Exception as exc:
        connection.send(("error", str(exc)))
    finally:
        connection.close()


class BackgroundJob:
    """剪贴板和还原共用进程生命周期；管道仅传短摘要和进度。"""
    def __init__(self, target, *args):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe(duplex=False)
        self.process = context.Process(target=target, args=(*args, child))
        self.finished, self.started, self.start_error = False, False, None
        def launch():
            try:
                self.process.start()
            except Exception as exc:
                self.start_error = str(exc)
            finally:
                child.close()
                self.started = True
        # Windows 创建冻结进程也可能耗时，不能在界面回调中等待。
        threading.Thread(target=launch, daemon=True).start()

    def poll(self):
        if not self.started:
            return []
        if self.start_error is not None:
            self.finished = True
            self.connection.close()
            return [("error", self.start_error)]
        messages = []
        for _ in range(32):
            if self.finished or not self.connection.poll():
                break
            try:
                event, value = self.connection.recv()
            except (EOFError, OSError):
                event, value = "error", "工作进程意外退出，请重新尝试"
            messages.append((event, value))
            if event in ("done", "error"):
                self.finished = True
                self.connection.close()
        if not self.finished and not self.process.is_alive() and not self.connection.poll():
            self.finished = True
            self.connection.close()
            messages.append(("error", "工作进程意外退出，请重新尝试"))
        if self.finished:
            self.process.join(timeout=0)
        return messages
