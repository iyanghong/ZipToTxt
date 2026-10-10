"""图形测试专用剪贴板提供者；Xvfb 使用独立剪贴板，宿主测试须自行备份。"""

import ctypes
import subprocess
import sys
from pathlib import Path


def main():
    if sys.argv[1] == "--generate":
        import os
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from zip_to_txt import encrypt_path
        base, size, part_size = Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
        source = base / "大文本原文件.bin"
        with source.open("wb") as stream:
            while size:
                chunk = os.urandom(min(1024 * 1024, size))
                stream.write(chunk)
                size -= len(chunk)
        encrypt_path(source, "粘贴密码", part_size_mb=part_size)
        return
    source, ready = map(Path, sys.argv[1:3])
    if sys.platform == "darwin":
        with source.open("rb") as stream:
            subprocess.run(["/usr/bin/pbcopy"], stdin=stream, check=True)
        ready.touch()
    elif sys.platform == "win32":
        from ctypes import wintypes
        user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
        kernel.GlobalAlloc.argtypes, kernel.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
        kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [wintypes.HGLOBAL], ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        user.SetClipboardData.argtypes, user.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
        handle = kernel.GlobalAlloc(0x42, (source.stat().st_size + 1) * 2)
        pointer = kernel.GlobalLock(handle)
        assert pointer
        offset = 0
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                data = chunk.decode("ascii").encode("utf-16-le")
                ctypes.memmove(pointer + offset, data, len(data))
                offset += len(data)
        kernel.GlobalUnlock(handle)
        assert user.OpenClipboard(None)
        try:
            assert user.EmptyClipboard()
            assert user.SetClipboardData(13, handle)
        finally:
            user.CloseClipboard()
        ready.touch()
    else:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(source.read_text(encoding="ascii"))
        root.update()
        ready.touch()
        root.mainloop()


if __name__ == "__main__":
    main()
