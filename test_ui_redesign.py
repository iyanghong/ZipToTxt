"""真实字节进度、错误清理、主窗口、设置记忆与结果操作检查。"""

import base64
import gc
import hashlib
import os
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import zip_to_txt as core


def run_progress(base):
    source = base / "进度测试"
    (source / "空目录").mkdir(parents=True)
    (source / "随机.bin").write_bytes(os.urandom(5_500_000))
    (source / "零字节.txt").write_bytes(b"")
    compressed, restored_events = [], []
    parts = core.encrypt_path(source, "进度密码", part_size_mb=1, on_progress=compressed.append)
    for part in parts.glob("*.txt"):
        part.write_bytes(part.read_bytes() + b"\0")
    restored = core.decrypt_path(parts, "进度密码", on_progress=restored_events.append)
    assert hashlib.sha256((restored / source.name / "随机.bin").read_bytes()).digest() == hashlib.sha256(
        (source / "随机.bin").read_bytes()).digest()
    assert (restored / source.name / "空目录").is_dir()
    assert (restored / source.name / "零字节.txt").stat().st_size == 0
    for events, phase, total in ((compressed, "压缩文件", 5_500_000),
            (restored_events, "校验解密 TXT", sum(p.stat().st_size for p in parts.glob("*.txt"))),
            (restored_events, "解压文件", 5_500_000)):
        values = [event for event in events if event.phase == phase]
        assert values[0].processed == 0 and values[0].total == total
        assert values[-1].finished and values[-1].processed == values[-1].total == total
        assert any(0 < event.processed < total for event in values), f"{phase} 应提供真实中间进度"
        assert [event.processed for event in values] == sorted(event.processed for event in values)
    encrypted = [event for event in compressed if event.phase == "加密生成 TXT"]
    actual_zip_size = sum(len(base64.b64decode(p.read_bytes()[:-1])) - core.HEADER.size - 16 for p in parts.glob("*.txt"))
    assert encrypted[0].total == actual_zip_size and encrypted[-1].processed == actual_zip_size
    assert any(0 < event.processed < actual_zip_size for event in encrypted)
    assert compressed[0].phase == "扫描文件" and compressed[0].total is None

    for name, directory in (("空目录归档", True), ("空文件归档", False)):
        item = base / name
        item.mkdir() if directory else item.write_bytes(b"")
        events = []
        empty_parts = core.encrypt_path(item, "密码", on_progress=events.append)
        core.decrypt_path(empty_parts, "密码", on_progress=events.append)
        for phase in ("压缩文件", "解压文件"):
            last = [event for event in events if event.phase == phase][-1]
            assert last.finished and last.processed == last.total == 0

    failure = base / "失败目录"
    failure.mkdir()
    events = []
    try:
        core.decrypt_path(parts, "错密码", output_dir=failure, on_progress=events.append)
    except ValueError:
        pass
    else:
        raise AssertionError("错误密码必须失败")
    assert not (failure / "restored").exists() and not any(event.finished for event in events)

    # 制造认证有效、ZIP CRC 损坏的分片，验证分块解压仍执行 CRC 和清理。
    corrupt_zip = base / "坏.zip"
    with zipfile.ZipFile(corrupt_zip, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("文件.bin", b"A" * 3_500_000)
    data = bytearray(corrupt_zip.read_bytes())
    data[100] ^= 1
    bad = base / "坏分片"
    bad.mkdir()
    salt, archive_id = os.urandom(16), os.urandom(8)
    header = core.HEADER.pack(core.MAGIC, salt, archive_id, 1, 1)
    encrypted_data = core.AESGCM(core.key_from_password("密码", salt)).encrypt(
        archive_id + (1).to_bytes(4, "big"), bytes(data), header)
    (bad / "part-000001.txt").write_bytes(base64.b64encode(header + encrypted_data))
    events.clear()
    try:
        core.decrypt_path(bad, "密码", on_progress=events.append)
    except zipfile.BadZipFile:
        pass
    else:
        raise AssertionError("损坏 ZIP 必须失败")
    assert not (bad / "restored").exists()
    assert not any(event.finished and event.phase == "解压文件" for event in events)
    assert any(event.phase == "解压文件" and event.processed > 0 for event in events)


def run_gui(base):
    import tkinter as tk
    import zip_to_txt_gui as gui
    from zip_to_txt import Progress

    config = base / "设置" / "settings.json"
    output = base / "GUI输出"
    output.mkdir()
    source = base / "界面大文件.bin"
    source.write_bytes(os.urandom(20 * 1024 * 1024))
    gui.save_preferences(config, {"source_dir": str(base), "output_dir": str(output), "part_size_mb": 3,
                                  "password": "不能保存", "confirmation": "不能保存"})
    assert "password" not in config.read_text() and "confirmation" not in config.read_text()
    root = tk.Tk()
    app = gui.App(root, config_path=config)
    messages, samples = [], []

    def wait_until(predicate):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            root.update()
            if app.busy and str(app.progress.cget("mode")) == "determinate":
                samples.append((float(app.progress["value"]), app.byte_progress.get()))
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("任务超时")

    try:
        with patch.object(gui.messagebox, "showerror", side_effect=lambda *a, **kw: messages.append(a)), \
             patch.object(gui.messagebox, "showinfo", side_effect=lambda *a, **kw: messages.append(a)):
            root.update()
            assert app.form_canvas.winfo_ismapped() and app.path_card.winfo_ismapped(), "首次启动必须显示路径表单"
            assert app.output_dir.get() == str(output) and app.part_size_mb.get() == "3"
            assert not app.password.get() and not app.confirmation.get()
            assert app.encrypt_choice.instate(["selected"])
            app.show_button.invoke()
            assert app.password_entry.cget("show") == app.confirm_entry.cget("show") == ""
            app.show_button.invoke()
            assert app.password_entry.cget("show") == "*"
            root.geometry("860x600")
            root.update()
            assert app.form_stacked, "小窗口应纵向排列参数卡片"
            app.form_canvas.yview_moveto(1)
            root.update()
            assert app.run_button.winfo_rooty() + app.run_button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
            root.geometry("1060x760")
            root.update()
            assert not app.form_stacked
            app.show_progress(Progress("解压文件", 0, 0))
            assert app.progress_text.get() == "0%"
            app.show_progress(Progress("解压文件", 0, 0, finished=True))
            assert app.progress_text.get() == "100%"
            app.path.set(str(source))
            app.password.set("界面密码")
            app.confirmation.set("界面密码")
            app.show_button.invoke()
            app.run_button.invoke()
            assert app.busy and str(app.path_entry.cget("state")) == "disabled"
            heartbeat = []
            root.after(30, lambda: heartbeat.append(True))
            wait_until(lambda: not app.busy)
            assert app.status.get() == "处理完成", messages
            assert heartbeat, "归档期间界面定时回调必须运行"
            assert any(0 < value < 100 and " / " in text for value, text in samples), samples
            assert app.status.get() == "处理完成" and app.progress_text.get() == "100%"
            assert not app.password.get() and not app.confirmation.get() and not app.password_visible
            parts = Path(app.output.get())
            assert parts.is_dir() and app.result_frame.winfo_ismapped()
            app.copy_button.invoke()
            assert root.clipboard_get() == str(parts)
            assert str(app.open_button.cget("state")) == "normal"
            assert not messages, "成功直接显示结果，不再弹成功对话框"
            app.decrypt_choice.invoke()
            assert app.mode.get() == "decrypt" and app.decrypt_choice.instate(["selected"])
            assert not app.size_frame.winfo_ismapped() and not app.confirm_entry.winfo_ismapped()
            app.path.set(str(parts))
            app.password.set("错密码")
            app.run_button.invoke()
            wait_until(lambda: not app.busy)
            assert app.status.get() == "处理失败" and app.progress_text.get() != "100%" and not app.output.get()
            assert not (output / "restored").exists()
            app.password.set("界面密码")
            app.run_button.invoke()
            wait_until(lambda: not app.busy)
            restored = Path(app.output.get()) / source.name
            assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(restored.read_bytes()).digest()
            assert app.progress_text.get() == "100%" and not app.password.get()
            app.remember_settings()
            data = gui.load_preferences(config)
            assert data["part_size_mb"] == 3 and data["source_dir"] == str(parts)
            assert "界面密码" not in config.read_text(encoding="utf-8")
    finally:
        app.close() if not app.busy else root.destroy()
    # 多个 Tk 根窗口只出现在测试中；在主线程释放上一轮解释器，避免工作线程触发循环回收。
    del app, root
    gc.collect()
    config.write_text("损坏的配置", encoding="utf-8")
    assert not gui.load_preferences(config)
    root = tk.Tk()
    app = gui.App(root, config_path=config)
    try:
        root.update_idletasks()
        assert app.part_size_mb.get() == "50" and not app.output_dir.get()
    finally:
        app.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        run_progress(base)
        run_gui(base)
    print("真实字节进度、零字节、CRC／密码失败清理、侧栏控件、设置记忆和结果操作检查通过")
