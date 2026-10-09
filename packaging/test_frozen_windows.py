"""在独立启动 EXE 后运行，确认真正的主窗口已经出现。

使用 Wine 时先用 wine start /unix 启动 EXE，随后用 Windows Python 执行本检查。
不要从 Python 启动 EXE：父进程的标准流可能掩盖启动器的问题。
"""

import ctypes
import time
from ctypes import wintypes

user = ctypes.windll.user32
callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
user.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
user.IsWindowVisible.argtypes = [wintypes.HWND]
windows = []
errors = []


def title(hwnd):
    text = ctypes.create_unicode_buffer(user.GetWindowTextLengthW(hwnd) + 1)
    user.GetWindowTextW(hwnd, text, len(text))
    return text.value


@callback
def child(hwnd, _param):
    errors.append(title(hwnd))
    return True


@callback
def window(hwnd, _param):
    text = title(hwnd)
    if user.IsWindowVisible(hwnd):
        windows.append(text)
    if text == "Error":
        user.EnumChildWindows(hwnd, child, 0)
    return True


deadline = time.monotonic() + 40
while time.monotonic() < deadline:
    windows.clear()
    errors.clear()
    user.EnumWindows(window, 0)
    assert not errors, "启动错误：" + "；".join(errors)
    if "ZIP 文本归档" in windows:
        break
    time.sleep(0.2)
assert "ZIP 文本归档" in windows, "未找到实际 EXE 主窗口；窗口：" + repr(windows)
print("实际 Windows EXE 主窗口已打开，无启动错误弹窗")
