"""python test_git_archive.py；带 Tk 的环境加 --gui 验证真实界面。"""

import gc
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from git_archive import branches_for, changed_files, commits_for, date_bounds, repository_for
from zip_to_txt import decrypt_path, encrypt_path, inspect_files


def expect_error(action, error=ValueError):
    try:
        action()
    except error:
        return
    raise AssertionError("预期操作报错")


def run(gui=False):
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        repo = base / "项目"
        repo.mkdir()

        def command(*args, environment=None):
            env = os.environ.copy()
            env.update(environment or {})
            return subprocess.run([shutil.which("git"), "-C", str(repo), *args],
                                  check=True, capture_output=True, env=env).stdout.decode().strip()

        def commit(subject, author, email, date, author_date=None):
            command("add", "-A")
            command("commit", "-m", subject, environment={
                "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": email,
                "GIT_AUTHOR_DATE": author_date or date,
                "GIT_COMMITTER_DATE": date,
            })
            return command("rev-parse", "HEAD")

        command("init", "-b", "main")
        command("config", "user.name", "提交者")
        command("config", "user.email", "committer@example.test")
        scope = repo / "范围[1]"
        scope.mkdir()
        (scope / "重复.txt").write_text("最初版本", encoding="utf-8")
        (scope / "旧 名称.txt").write_text("被重命名", encoding="utf-8")
        (scope / "删除.txt").write_text("会被删除", encoding="utf-8")
        (repo / "范围1").mkdir()
        (repo / "范围1" / "其他.txt").write_text("不能误匹配", encoding="utf-8")
        first = commit("初始文件", "Alice", "alice@example.test", "2026-10-05T12:00:00+0800")
        (scope / "重复.txt").write_text("第二版", encoding="utf-8")
        (scope / "旧 名称.txt").rename(scope / "新 名称.txt")
        (scope / "删除.txt").unlink()
        second = commit("修改、重命名、删除", "Bob", "bob@example.test", "2026-10-06T12:00:00+0800",
                        author_date="2001-01-01T12:00:00+0800")
        (repo / "范围1" / "其他.txt").write_text("仅修改范围外", encoding="utf-8")
        outside = commit("其他目录", "Alice", "alice@example.test", "2026-10-07T10:00:00+0800")
        command("checkout", "-b", "feature")
        (scope / "合入.txt").write_text("分支文件", encoding="utf-8")
        feature = commit("支线文件", "Alice", "alice@example.test", "2026-10-07T11:00:00+0800")
        command("checkout", "main")
        (repo / "主线.txt").write_text("不应进入合并差异", encoding="utf-8")
        commit("主线文件", "Bob", "bob@example.test", "2026-10-07T12:00:00+0800")
        command("merge", "--no-ff", "feature", "-m", "合并支线", environment={
            "GIT_AUTHOR_DATE": "2026-10-07T13:00:00+0800", "GIT_COMMITTER_DATE": "2026-10-07T13:00:00+0800",
        })
        merge = command("rev-parse", "HEAD")
        command("update-ref", "refs/remotes/origin/main", merge)
        (scope / "重复.txt").write_text("当前磁盘上的未提交内容", encoding="utf-8")
        (scope / "未跟踪.txt").write_text("不应打包", encoding="utf-8")

        assert repository_for(scope) == repo.resolve()
        branches, current = branches_for(scope)
        assert branches[current] == "refs/heads/main"
        assert "refs/remotes/origin/main" in branches.values()
        rows = commits_for(scope, "refs/heads/main")
        assert {first, second, feature, merge}.issubset({row.sha for row in rows})
        assert outside not in {row.sha for row in rows}
        assert {row.sha for row in commits_for(scope, "refs/heads/main", author="BOB@EXAMPLE")} == {second}
        second_day = datetime.fromtimestamp(next(row.timestamp for row in rows if row.sha == second)).strftime("%Y-%m-%d")
        assert {row.sha for row in commits_for(scope, "refs/heads/main", second_day, second_day)} == {second}
        assert not commits_for(scope, "refs/heads/main", "2001-01-01", "2001-01-01")
        expect_error(lambda: date_bounds("2026-02-30", ""))
        expect_error(lambda: date_bounds("2026-10-07", "2026-10-06"))
        assert changed_files(scope, [merge]) == ["合入.txt"]
        names = changed_files(scope, [first, second, second])
        assert names == ["删除.txt", "新 名称.txt", "旧 名称.txt", "重复.txt"]
        files, missing = inspect_files(scope, names)
        assert files == ["新 名称.txt", "重复.txt"]
        assert missing == ["删除.txt", "旧 名称.txt"]
        parts = encrypt_path(scope, "真实测试密码", files=files, part_size_mb=1)
        restored = decrypt_path(parts, "真实测试密码") / scope.name
        assert {item.relative_to(restored).as_posix() for item in restored.rglob("*") if item.is_file()} == set(files)
        assert (restored / "重复.txt").read_bytes() == (scope / "重复.txt").read_bytes()
        assert not (restored / ".git").exists()
        assert not (restored / "未跟踪.txt").exists()
        empty_parent = base / "空清单输出"
        empty_parent.mkdir()
        expect_error(lambda: encrypt_path(scope, "密码", files=[], output_dir=empty_parent))
        expect_error(lambda: inspect_files(scope, ["../主线.txt"]))
        expect_error(lambda: inspect_files(scope, ["/etc/passwd"]))
        expect_error(lambda: inspect_files(scope, ["重复.txt\\非法"]))
        (scope / "跨平台.txt").write_text("a")
        (scope / "跨平台.TXT").write_text("b")
        # 大小写敏感文件系统才会产生两项；检查名字冲突不依赖文件内容。
        expect_error(lambda: inspect_files(scope, ["跨平台.txt", "跨平台.TXT"]))
        target = base / "外部"
        target.mkdir()
        (target / "私有.txt").write_text("目录外内容")
        try:
            (scope / "链接").symlink_to(target, target_is_directory=True)
        except OSError:
            pass  # Windows 未授予符号链接权限时仍执行其他边界检查。
        else:
            expect_error(lambda: inspect_files(scope, ["链接/私有.txt"]))
            alias = base / "入口链接"
            alias.symlink_to(scope, target_is_directory=True)
            expect_error(lambda: changed_files(alias, [first]))
            expect_error(lambda: inspect_files(alias, ["重复.txt"]))
        with patch("git_archive.shutil.which", return_value=None):
            expect_error(lambda: repository_for(scope))
        missing_file = scope / "新 名称.txt"
        missing_file.unlink()
        failure_parent = base / "失败输出"
        failure_parent.mkdir()
        expect_error(lambda: encrypt_path(scope, "密码", files=files, output_dir=failure_parent))
        assert not (failure_parent / (scope.name + ".txtparts")).exists()
        missing_file.write_text("恢复当前内容", encoding="utf-8")

        if gui:
            run_gui(base, repo, scope, first, second, outside)


def run_gui(base, repo, scope, first, second, outside):
    import tkinter as tk
    import zip_to_txt_gui as gui

    messages = []
    root = tk.Tk()
    app = gui.App(root, config_path=base / "gui-settings.json")

    def wait_until(predicate, seconds=15):
        end = time.monotonic() + seconds
        ticks = 0
        while time.monotonic() < end:
            root.update()
            ticks += 1
            if predicate():
                return ticks
            time.sleep(0.01)
        raise AssertionError("界面未在期限内完成任务")

    try:
        with patch.object(gui.messagebox, "showerror", side_effect=lambda *a, **kw: messages.append(a)), \
             patch.object(gui.messagebox, "showinfo", side_effect=lambda *a, **kw: messages.append(a)):
            app.path.set(str(scope))
            wait_until(lambda: app.git_ready)
            app.choose_commits()
            picker = app.picker
            wait_until(lambda: not picker.loading and len(picker.rows) > 0)
            geometry = picker.window.geometry()
            picker.window.geometry("980x600")
            root.update()
            first_row = picker.tree.bbox(picker.tree.get_children()[0])
            assert first_row and first_row[1] + first_row[3] <= picker.tree.winfo_height(), (
                f"小窗口须显示完整提交行：行 {first_row}，表高 {picker.tree.winfo_height()}，"
                f"分隔线 {picker.panes.sashpos(0)}，总高 {picker.panes.winfo_height()}")
            picker.window.geometry(geometry)
            root.update()
            heartbeat = []
            real_query = gui.commits_for

            def slow_query(*args, **kwargs):
                time.sleep(0.3)
                return real_query(*args, **kwargs)

            with patch.object(gui, "commits_for", side_effect=slow_query):
                picker.load()
                root.after(50, lambda: heartbeat.append(picker.loading))
                wait_until(lambda: not picker.loading)
            assert heartbeat == [True], "查询期间 Tk 事件循环应继续响应"
            assert picker.tree.heading("author", "text") == "用户"
            assert "alice@example.test" in picker.tree.set(first, "author")
            real_preview = gui.preview_files

            def slow_preview(*args):
                time.sleep(0.35)
                return real_preview(*args)

            with patch.object(gui, "preview_files", side_effect=slow_preview):
                picker.toggle(first)
                wait_until(lambda: picker.preview_job_active)
                assert str(picker.accept_button.cget("state")) == "disabled"
                assert str(picker.all_button.cget("state")) == "normal", "计算清单期间仍可修改勾选"
                picker.toggle(first)
                picker.toggle(second)
                heartbeat.clear()
                root.after(50, lambda: heartbeat.append(True))
                wait_until(lambda: picker.preview_ok and picker.preview_commits == (second,))
                assert heartbeat == [True]
                assert picker.file_list.files == ["新 名称.txt", "重复.txt"], "旧任务不能覆盖最新选择"
            picker.clear()
            picker.toggle(first)
            picker.toggle(second)
            root.update()
            assert set(picker.selected) == {first, second}
            original_branch = picker.branch.get()
            picker.branch_keyword.set("FEATURE")
            assert list(picker.branch_box.cget("values")) == ["本地：feature"]
            assert picker.branch.get() == original_branch and set(picker.selected) == {first, second}
            picker.branch_keyword.set("not-a-branch")
            assert not picker.branch_box.cget("values")
            assert "无匹配" in picker.branch_count.get()
            assert picker.branch.get() == original_branch
            picker.branch_keyword.set("ORIGIN/MAIN")
            assert list(picker.branch_box.cget("values")) == ["远程：origin/main"]
            picker.branch_keyword.set("")
            second_day = datetime.fromtimestamp(picker.visible[second].timestamp).strftime("%Y-%m-%d")
            with patch.object(gui, "date_shortcut", return_value=(second_day, second_day)):
                picker.shortcut_buttons[0].invoke()
            wait_until(lambda: not picker.loading)
            assert set(picker.visible) == {second} and set(picker.selected) == {first, second}
            assert picker.start_date.get() == picker.end_date.get() == second_day
            picker.shortcut_buttons[-1].invoke()
            wait_until(lambda: not picker.loading)
            assert not picker.start_date.get() and not picker.end_date.get()
            assert set(picker.selected) == {first, second}
            picker.author.set("Bob")
            picker.load()
            wait_until(lambda: not picker.loading)
            assert set(picker.visible) == {second}
            assert set(picker.selected) == {first, second}
            assert "列表外 1 个" in picker.count.get()
            picker.invert_button.invoke()
            assert set(picker.selected) == {first}, "反选仅操作匹配结果，保留筛选范围外的选择"
            picker.all_button.invoke()
            assert set(picker.selected) == {first, second}
            picker.start_date.set("2026-02-30")
            picker.filter_button.invoke()
            wait_until(lambda: not picker.loading)
            assert not picker.query_ok and str(picker.accept_button.cget("state")) == "disabled"
            picker.start_date.set("")
            picker.filter_button.invoke()
            wait_until(lambda: not picker.loading and picker.preview_ok)
            assert picker.query_ok and str(picker.accept_button.cget("state")) == "normal"
            picker.branch.set("本地：feature")
            picker.change_branch()
            wait_until(lambda: not picker.loading)
            assert not picker.selected
            picker.branch.set("本地：main")
            picker.author.set("")
            picker.change_branch()
            wait_until(lambda: not picker.loading)
            picker.toggle(first)
            picker.toggle(second)
            root.update()
            wait_until(lambda: picker.preview_ok)
            assert not app.password.get() and not app.git_commits, "预览不需要先填密码"
            assert picker.file_list.files == ["新 名称.txt", "重复.txt"]
            assert picker.file_list.missing == ["删除.txt", "旧 名称.txt"]
            assert picker.file_list.total_size == sum((scope / name).stat().st_size for name in picker.file_list.files)
            assert set(picker.selected) == {first, second}
            picker.accept()
            assert set(app.git_commits) == {first, second}
            app.choose_commits()
            picker = app.picker
            wait_until(lambda: not picker.loading)
            assert set(picker.selected) == {first, second}, "重新打开应回填确认的选择"
            picker.close()
            destination = base / "界面输出"
            destination.mkdir()
            app.output_dir.set(str(destination))
            app.password.set("界面测试密码")
            app.confirmation.set("界面测试密码")
            # 文件范围变化才重新确认；取消不会产生输出。
            original = (scope / "新 名称.txt").read_bytes()
            (scope / "新 名称.txt").unlink()
            app.start()
            wait_until(lambda: hasattr(app, "preview") and app.preview.window.winfo_exists())
            assert app.preview.files == ["重复.txt"]
            assert "新 名称.txt" in app.preview.missing
            app.preview.cancel()
            assert not app.busy and not list(destination.iterdir())
            # 提前预览以后磁盘内容改变，最终清单和归档都必须读取最新内容。
            (scope / "新 名称.txt").write_bytes(original)
            (scope / "重复.txt").write_text("预览后改变的当前内容", encoding="utf-8")
            app.password.set("界面测试密码")
            app.confirmation.set("界面测试密码")
            app.start()
            wait_until(lambda: not app.busy)
            assert not app.preview.window.winfo_exists(), "仅内容变化时直接读取当前内容，不重复确认范围"
            assert app.output.get()
            restored = decrypt_path(Path(app.output.get()), "界面测试密码") / scope.name
            assert (restored / "重复.txt").read_bytes() == (scope / "重复.txt").read_bytes()
            assert not (restored / "未跟踪.txt").exists()
            # 当前已不存在的文件全部跳过时，不生成空归档。
            command = [shutil.which("git"), "--literal-pathspecs", "-C", str(repo)]
            subprocess.run([*command, "checkout", "-b", "deleted-only"], check=True, capture_output=True)
            (scope / "只有删除.txt").write_text("待删除")
            subprocess.run([*command, "add", "范围[1]/只有删除.txt"], check=True, capture_output=True)
            subprocess.run([*command, "commit", "-m", "新增后删除测试"], check=True, capture_output=True)
            (scope / "只有删除.txt").unlink()
            subprocess.run([*command, "add", "范围[1]/只有删除.txt"], check=True, capture_output=True)
            subprocess.run([*command, "commit", "-m", "纯删除"], check=True, capture_output=True)
            deleted = subprocess.run([*command, "rev-parse", "HEAD"], check=True, capture_output=True).stdout.decode().strip()
            app.choose_commits()
            picker = app.picker
            wait_until(lambda: not picker.loading)
            picker.branch.set("本地：deleted-only")
            picker.change_branch()
            wait_until(lambda: not picker.loading)
            picker.clear_button.invoke()
            picker.toggle(deleted)
            wait_until(lambda: picker.preview_data is not None)
            assert not picker.file_list.files and picker.file_list.missing == ["只有删除.txt"]
            assert str(picker.accept_button.cget("state")) == "disabled"
            picker.accept()
            assert not picker.closed, "全缺失不能确认空包"
            picker.close()
            app.use_commits((deleted,))
            app.password.set("密码")
            app.confirmation.set("密码")
            app.start()
            wait_until(lambda: not app.busy)
            assert "没有可打包文件" in messages[-1][1]
            app.path.set(str(repo / "范围1"))
            assert not app.git_commits
            wait_until(lambda: app.git_ready)
            with patch("git_archive.shutil.which", return_value=None):
                app.path_changed()
                wait_until(lambda: "未找到系统 Git" in app.git_status.get())
                assert not app.git_ready
                assert str(app.git_button.cget("state")) == "disabled"
                assert str(app.run_button.cget("state")) == "normal"
                ordinary = base / "普通目录"
                ordinary.mkdir()
                (ordinary / "正常.txt").write_text("没有 Git 仍可压缩", encoding="utf-8")
                app.path.set(str(ordinary))
                app.password.set("普通密码")
                app.confirmation.set("普通密码")
                app.start()
                wait_until(lambda: not app.busy)
                assert app.output.get()
                restored = decrypt_path(Path(app.output.get()), "普通密码") / ordinary.name
                assert (restored / "正常.txt").read_bytes() == (ordinary / "正常.txt").read_bytes()

            # 用真实的 205 次文件修改核对分页，确认没有悄悄截断历史。
            history = base / "分页仓库"
            history.mkdir()
            prefix = [shutil.which("git"), "-C", str(history)]
            for args in (("init", "-b", "main"), ("config", "user.name", "分页作者"),
                         ("config", "user.email", "pages@example.test")):
                subprocess.run([*prefix, *args], check=True, capture_output=True)
            for number in range(205):
                (history / "变化.txt").write_text(str(number))
                subprocess.run([*prefix, "add", "变化.txt"], check=True, capture_output=True)
                subprocess.run([*prefix, "commit", "-m", f"修改 {number}"], check=True, capture_output=True)
            app.path.set(str(history))
            wait_until(lambda: app.git_ready)
            app.choose_commits()
            picker = app.picker
            wait_until(lambda: not picker.loading and len(picker.rows) == 205)
            assert len(picker.visible) == 200
            chosen = next(iter(picker.visible))
            picker.toggle(chosen)
            root.update()
            picker.more()
            root.update()
            assert len(picker.visible) == 205 and set(picker.selected) == {chosen}
            picker.clear_button.invoke()
            picker.load()
            wait_until(lambda: not picker.loading)
            assert len(picker.visible) == 200
            picker.all_button.invoke()
            assert len(picker.selected) == 205 and "列表外 5 个" in picker.count.get()
            picker.invert_button.invoke()
            assert not picker.selected
            picker.invert_button.invoke()
            assert len(picker.selected) == 205
            picker.clear_button.invoke()
            assert not picker.selected
            # 映射实际窗口，通过鼠标事件点击勾选列，空格可再次取消。
            root.deiconify()
            picker.window.deiconify()
            root.update()
            item = next(iter(picker.visible))
            x, y, width, height = picker.tree.bbox(item, "pick")
            picker.tree.event_generate("<Button-1>", x=x + width // 2, y=y + height // 2)
            root.update()
            assert set(picker.selected) == {item}
            picker.tree.focus_force()
            picker.tree.event_generate("<space>")
            root.update()
            assert not picker.selected
            picker.close()
    finally:
        app.busy = False
        app.close()


if __name__ == "__main__":
    gui = "--gui" in sys.argv
    gc_enabled = gc.isenabled()
    if gui:
        gc.disable()
    try:
        run(gui=gui)
    finally:
        gc.collect()
        if gc_enabled:
            gc.enable()
    print("Git 当前文件归档、筛选及边界检查通过" + ("；真实 Tk 界面检查通过" if "--gui" in sys.argv else ""))
