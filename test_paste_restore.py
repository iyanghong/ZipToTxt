"""粘贴还原的安全边界，以及真实剪贴板多片大文本／GUI 响应测试。"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import paste_restore as paste
import zip_to_txt as core


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def resident_bytes():
    if sys.platform == "darwin":
        return int(subprocess.check_output(["/bin/ps", "-p", str(os.getpid()), "-o", "rss="]).strip()) * 1024
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        values = Counters()
        values.cb = ctypes.sizeof(values)
        ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        assert ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(values), values.cb)
        return values.WorkingSetSize
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024


def run_validation(base):
    source = base / "小文件.txt"
    source.write_text("粘贴还原测试" * 100, encoding="utf-8")
    parts = core.encrypt_path(source, "测试密码")
    encoded = (parts / "part-000001.txt").read_bytes()
    spaced = b" \r\n" + b"\t \r\n".join(encoded[i:i+71] for i in range(0, len(encoded), 71)) + b"\0\n"
    info = paste.store_part((spaced[i:i+13] for i in range(0, len(spaced), 13)), base / "clean.txt")
    assert Path(info["path"]).read_bytes() == encoded
    assert paste.collection_status([info])[0]
    assert not paste.collection_status([info, info])[0]
    assert not paste.collection_status([{**info, "count": 2}])[0]
    assert not paste.collection_status([info, {**info, "archive_id": "00" * 8}])[0]
    assert not paste.collection_status([])[0]
    for label, data in (("空白", b" \r\n\t"), ("非法字符", encoded + "说明".encode()),
                        ("多片合并", encoded + encoded), ("多个NUL", encoded + b"\0\0"),
                        ("截断", encoded[:-1])):
        target = base / f"{label}.txt"
        try:
            paste.store_part([data], target)
        except ValueError:
            assert not target.exists()
        else:
            raise AssertionError(label)
    with patch.object(paste, "MAX_TEXT_LIMIT", len(encoded) - 1):
        try:
            paste.store_part([encoded], base / "超限.txt")
        except ValueError:
            pass
        else:
            raise AssertionError("超限必须失败")
    protected = base / "已有文件.txt"
    protected.write_bytes("不能覆盖".encode())
    try:
        paste.store_part([encoded], protected)
    except FileExistsError:
        assert protected.read_bytes() == "不能覆盖".encode()
    else:
        raise AssertionError("不能覆盖已有文件")


def run_gui(base, large=False):
    import tkinter as tk
    import zip_to_txt_gui as gui
    source = base / "大文本原文件.bin"
    size = 260_000_000 if large else 1_700_000
    fixture = Path(__file__).resolve().parent / "packaging" / "paste_fixture.py"
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    # 测试数据由外部进程生成，避免生成归档的内存峰值污染 GUI 的测量。
    subprocess.run([sys.executable, str(fixture), "--generate", str(base), str(size), "120" if large else "1"],
                   check=True, **options)
    parts = sorted(source.with_name(source.name + ".txtparts").glob("*.txt"))
    if large:
        assert len(parts) == 3 and all(path.stat().st_size > 100_000_000 for path in parts)
    output = base / "指定输出"
    output.mkdir()
    config = base / "settings.json"
    root = tk.Tk()
    app = gui.App(root, config_path=config)
    provider, backup, timings, errors, memory = None, None, {}, [], []
    if sys.platform == "darwin":
        backup = base / "原剪贴板"
        with backup.open("wb") as stream:
            subprocess.run(["/usr/bin/pbpaste"], stdout=stream, check=True)
    heartbeat, active_stage, last_tick = [], "", time.monotonic()
    def tick():
        nonlocal last_tick
        now = time.monotonic()
        if active_stage:
            heartbeat.append((active_stage, now - last_tick))
        last_tick = now
        root.after(20, tick)
    root.after(20, tick)

    def wait_until(predicate, timeout=300):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            root.update()
            if predicate():
                return
            time.sleep(0.003)
        raise AssertionError(f"任务超时：{app.status.get()} / {app.paste_parts.summary.get()}")

    def set_clipboard(path):
        nonlocal provider
        if provider is not None and provider.poll() is None:
            provider.terminate()
            provider.wait(timeout=10)
        ready = base / "clipboard-ready"
        ready.unlink(missing_ok=True)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        provider = subprocess.Popen([sys.executable, str(fixture), str(path), str(ready)], **options)
        wait_until(ready.exists)

    try:
        with patch.object(gui.messagebox, "showerror", side_effect=lambda *a, **kw: errors.append(a)):
            root.update()
            app.paste_choice.invoke()
            root.update()
            assert app.mode.get() == "paste" and app.paste_choice.instate(["selected"])
            assert app.paste_parts.winfo_ismapped() and not app.path_entry.winfo_ismapped()
            app.output_dir.set(str(output))
            memory.append(resident_bytes())
            for index, path in enumerate(reversed(parts)):
                if index:
                    app.paste_parts.add_button.invoke()
                card = app.paste_parts.cards[-1]
                set_clipboard(path)
                active_stage, last_tick = f"粘贴第{index+1}项", time.monotonic()
                start = time.monotonic()
                card["entry"].event_generate("<<Paste>>")
                assert app.paste_parts.loading
                wait_until(lambda: not app.paste_parts.loading)
                timings[active_stage] = time.monotonic() - start
                heartbeat.append((active_stage, time.monotonic() - last_tick))
                active_stage = ""
                assert not card["error"], card["error"]
                assert card["info"]["size"] == path.stat().st_size
                assert len(card["preview"].get()) < 120
                assert card["info"]["index"] == len(parts) - index
                memory.append(resident_bytes())
                print(f"已真实粘贴输入项 {index+1}：{path.stat().st_size / 1_000_000:.2f} MB", flush=True)
            assert app.paste_parts.ready
            if os.environ.get("ZTXT_PASTE_SCREENSHOT") and sys.platform == "linux":
                root.geometry("1060x920+110+40")
                root.lift()
                painted = []
                root.after(300, lambda: painted.append(True))
                wait_until(lambda: painted)
                subprocess.run(["env", "LD_LIBRARY_PATH=/capture-lib", "/capture-bin/xwd", "-silent", "-root",
                                "-out", os.environ["ZTXT_PASTE_SCREENSHOT"]], check=True)
            # 替换失败保留原分片，但错误状态阻止还原；重新粘贴可恢复。
            invalid = base / "不是分片.txt"
            invalid.write_text("not a valid part!", encoding="ascii")
            first = app.paste_parts.cards[0]
            previous = first["info"]
            set_clipboard(invalid)
            first["paste"].invoke()
            wait_until(lambda: not app.paste_parts.loading)
            assert first["error"] and not app.paste_parts.ready
            assert first["info"] == previous and Path(previous["path"]).is_file()
            set_clipboard(parts[-1])
            first["paste"].invoke()
            wait_until(lambda: not app.paste_parts.loading)
            assert not first["error"] and app.paste_parts.ready
            assert not Path(previous["path"]).exists()
            # 重复片会阻止开始；删除后不影响其他已粘贴的大分片。
            app.paste_parts.add_button.invoke()
            duplicate = app.paste_parts.cards[-1]
            duplicate["paste"].invoke()
            wait_until(lambda: not app.paste_parts.loading)
            assert not app.paste_parts.ready and "重复" in app.paste_parts.summary.get()
            duplicate["delete"].invoke()
            assert app.paste_parts.ready
            app.encrypt_choice.invoke()
            app.paste_choice.invoke()
            assert len(app.paste_parts.parts()) == len(parts)
            app.password.set("错误密码")
            active_stage, last_tick = "错误密码重试", time.monotonic()
            start = time.monotonic()
            app.run_button.invoke()
            wait_until(lambda: not app.busy)
            timings[active_stage] = time.monotonic() - start
            heartbeat.append((active_stage, time.monotonic() - last_tick))
            active_stage = ""
            assert app.status.get() == "处理失败" and len(app.paste_parts.parts()) == len(parts)
            assert not (output / "restored").exists()
            app.password.set("粘贴密码")
            active_stage, last_tick = "解密及解压", time.monotonic()
            start = time.monotonic()
            app.run_button.invoke()
            assert app.busy
            wait_until(lambda: not app.busy)
            timings[active_stage] = time.monotonic() - start
            heartbeat.append((active_stage, time.monotonic() - last_tick))
            active_stage = ""
            assert app.status.get() == "处理完成", errors
            assert digest(Path(app.output.get()) / source.name) == digest(source)
            assert app.progress_text.get() == "100%" and not app.password.get()
            assert not app.paste_parts.parts() and app.paste_parts.temporary is None
            assert "粘贴密码" not in config.read_text(encoding="utf-8")
            stages = {}
            for stage, delay in heartbeat:
                stages[stage] = max(delay, stages.get(stage, 0))
            assert all(stage in stages for stage in timings), stages
            # 250 ms 是本测试环境的验收阈值；记录实际值，不以“回调曾运行”代替响应检查。
            result = dict(platform=sys.platform, source_bytes=size, part_bytes=[p.stat().st_size for p in parts],
                          timings_seconds=timings, maximum_ui_delay_seconds=stages,
                          gui_resident_bytes_after_each_paste=memory,
                          restored_sha256=digest(source), heartbeat_samples=len(heartbeat))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if os.environ.get("ZTXT_PASTE_TEST_REPORT"):
                Path(os.environ["ZTXT_PASTE_TEST_REPORT"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            assert max(stages.values()) < 0.25, stages
            assert max(memory[1:]) - memory[0] < 64 * 1024 * 1024, memory
    finally:
        if provider is not None and provider.poll() is None:
            provider.terminate()
            provider.wait(timeout=10)
        if backup is not None:
            with backup.open("rb") as stream:
                subprocess.run(["/usr/bin/pbcopy"], stdin=stream, check=True)
        if not app.busy and not app.paste_parts.loading:
            app.close()
        else:
            root.destroy()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        run_validation(base)
        run_gui(base, large="--large" in sys.argv or os.environ.get("ZTXT_PASTE_LARGE") == "1")
    print("粘贴归一化、安全边界、真实剪贴板、乱序／重复／密码失败重试及还原校验通过", flush=True)
