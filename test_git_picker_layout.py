"""真实 Git / Tk 布局检查：两个列表占满宽度，长信息可查看全文。"""

import gc
import os
import shutil
import subprocess
import tempfile
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from git_archive import commits_for
import git_archive
import zip_to_txt_gui as gui


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def check_details(root, tree, expected, *, double_click=False):
    tree.focus(tree.get_children()[0])
    tree.focus_force()
    root.update()
    if double_click:
        x, y, width, height = tree.bbox(tree.get_children()[0], 'path')
        for timestamp in (2000, 2100):
            tree.event_generate('<ButtonPress-1>', x=x + 15, y=y + height // 2, time=timestamp)
            tree.event_generate('<ButtonRelease-1>', x=x + 15, y=y + height // 2, time=timestamp + 10)
            root.update()
    else:
        tree.event_generate('<Return>')
    root.update()
    dialogs = [widget for widget in descendants(root) if isinstance(widget, tk.Toplevel)
               and widget.title() in ('提交详情', '文件详情')]
    assert len(dialogs) == 1, '键盘或双击行应提供全文查看入口'
    dialog = dialogs[0]
    content = next(widget for widget in descendants(dialog) if isinstance(widget, tk.Text))
    text = content.get('1.0', 'end-1c')
    assert all(value in text for value in expected), '长路径、作者和说明不能在详情中截断'
    copy = next(widget for widget in descendants(dialog) if widget.winfo_class() == 'TButton'
                and widget.cget('text') == '复制全文')
    copy.invoke()
    assert dialog.clipboard_get() == text
    dialog.destroy()
    root.update()


def run():
    if shutil.which('git') is None:
        run_without_git()
        return
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / '布局仓库'
        source.mkdir()
        prefix = ['git', '-C', str(source)]
        for args in [('init', '-b', 'main'), ('config', 'user.name', '长用户姓名测试'),
                     ('config', 'user.email', 'developer.with.a.long.email@example.test')]:
            subprocess.run([*prefix, *args], check=True, capture_output=True)
        relative = 'java/com/example/digital/operations/service/implementation/VeryLongCustomerRetentionAndReportingServiceImplementation.java'
        path = source / relative
        path.parent.mkdir(parents=True)
        subject = '更新项目工时统计报表接口：补充项目明细、数据校验和长提交说明的完整显示'
        now = int(datetime(2026, 10, 8, 12, 0).timestamp())
        offsets = (-48 * 3600 - 1, -48 * 3600, -24 * 3600 - 1, -24 * 3600, -20 * 3600, 0, 1)
        commits = {}
        for offset in offsets:
            path.write_text(f'真实当前磁盘内容 {offset}', encoding='utf-8')
            subprocess.run([*prefix, 'add', '.'], check=True, capture_output=True)
            env = dict(os.environ, GIT_AUTHOR_DATE=f'{now + offset} +0000', GIT_COMMITTER_DATE=f'{now + offset} +0000')
            subprocess.run([*prefix, 'commit', '-m', subject], env=env, check=True, capture_output=True)
            commits[offset] = subprocess.run([*prefix, 'rev-parse', 'HEAD'], check=True,
                                             capture_output=True, text=True).stdout.strip()
        check_time_queries(source, commits, now)

        check_ui(base, source, relative, subject, commits, now)
        check_ui(base, source, relative, subject, commits, now, scaling=2.0)
    print('上下列表、长数据全文与复制、最近 24/48 小时边界、回填、小窗口和字体缩放检查通过')


def check_time_queries(source, commits, now):
    with patch('git_archive.time.time', return_value=now):
        for hours in (24, 48):
            expected = {sha for offset, sha in commits.items() if -hours * 3600 <= offset <= 0}
            actual = commits_for(source, 'refs/heads/main', recent_hours=hours)
            assert {row.sha for row in actual} == expected, f'{hours} 小时窗口起点、越界、当前、未来时间'
        with patch('git_archive.time.time', return_value=now + 2):
            actual = {row.sha for row in commits_for(source, 'refs/heads/main', recent_hours=48)}
            assert commits[1] in actual and commits[-48 * 3600] not in actual, '查询时刻变化应更新滚动范围'
        today = {row.sha for row in commits_for(source, 'refs/heads/main', '2026-10-08', '2026-10-08')}
        assert commits[-20 * 3600] not in today, '最近 24 小时与当天必须区分'


def run_without_git():
    """Wine 未安装 Git；仍验证实际包内筛选逻辑和 Tk 长数据布局。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / '布局仓库'
        source.mkdir()
        relative = 'java/com/example/digital/operations/service/implementation/VeryLongCustomerRetentionAndReportingServiceImplementation.java'
        path = source / relative
        path.parent.mkdir(parents=True)
        path.write_text('真实当前磁盘内容', encoding='utf-8')
        subject = '更新项目工时统计报表接口：补充项目明细、数据校验和长提交说明的完整显示'
        now = int(datetime(2026, 10, 8, 12, 0).timestamp())
        offsets = (-48 * 3600 - 1, -48 * 3600, -24 * 3600 - 1, -24 * 3600, -20 * 3600, 0, 1)
        commits = {offset: f'{index:040x}' for index, offset in enumerate(offsets, 1)}
        log = '\0'.join(value for offset, sha in commits.items()
                        for value in (sha, str(now + offset), '长用户姓名测试',
                                      'developer.with.a.long.email@example.test', subject)) + '\0'
        with patch.object(git_archive, 'repository_for', return_value=source.resolve()), \
             patch.object(git_archive, 'git', return_value=log.encode('utf-8')), \
             patch.object(gui, 'branches_for', return_value=({'本地：main': 'refs/heads/main'}, '本地：main')), \
             patch.object(gui, 'changed_files', return_value=[relative]):
            check_time_queries(source, commits, now)
            check_ui(base, source, relative, subject, commits, now)
            check_ui(base, source, relative, subject, commits, now, scaling=2.0)
    print('未安装 Git：固定提交数据的包内时间筛选、真实磁盘文件、Tk 布局、全文复制及缩放检查通过')


def check_ui(base, source, relative, subject, commits, now, scaling=None):
    root = tk.Tk()
    if scaling:
        root.tk.call('tk', 'scaling', scaling)
    app = gui.App(root, config_path=base / 'settings.json')
    accepted = []
    picker = gui.GitPicker(root, source, lambda _commits, state: accepted.append(state))

    def wait_until(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('真实提交查询或预览未及时完成')

    try:
        wait_until(lambda: not picker.loading)
        assert picker.query_ok
        picker.window.geometry('980x600')
        root.update()
        first = picker.tree.bbox(picker.tree.get_children()[0])
        assert first and first[1] + first[3] <= picker.tree.winfo_height(), '大窗口直接缩小，未勾选时也须显示完整提交行'
        picker.all_button.invoke()
        wait_until(lambda: picker.preview_ok)
        assert picker.preview_ok
        sizes = ('1240x820', '980x600') if scaling is None else ('1240x900',)
        for size in sizes:
            picker.window.geometry(size)
            root.update()
            root.update()
            width = picker.window.winfo_width()
            commit_width = picker.tree.winfo_width()
            file_width = picker.file_list.tree.winfo_width()
            assert min(commit_width, file_width) >= width * .85, (
                f'列表仍被挤窄：窗口 {width}px，提交 {commit_width}px，文件 {file_width}px')
            assert picker.file_list.tree.winfo_rooty() > picker.tree.winfo_rooty(), '提交在上，文件在下'
            for tree in (picker.tree, picker.file_list.tree):
                row = tree.bbox(tree.get_children()[0])
                assert row and row[1] + row[3] <= tree.winfo_height(), (
                    f'{size} 须显示完整数据行；{tree.winfo_height()=}, {row=}, '
                    f'分隔线={picker.panes.sashpos(0)}, 总高={picker.panes.winfo_height()}')
            row = picker.file_list.tree.bbox(picker.file_list.tree.get_children()[0], 'status')
            assert row[0] + row[2] <= picker.file_list.tree.winfo_width(), '文件状态不能被路径列挤出'
        check_details(root, picker.tree, [subject, '长用户姓名测试', 'developer.with.a.long.email@example.test'])
        check_details(root, picker.file_list.tree, [relative, '可打包'], double_click=True)
        picker.window.geometry('1240x900')
        root.update()
        sash = picker.panes.sashpos(0)
        picker.panes.sashpos(0, sash + 20)
        root.update()
        assert picker.panes.sashpos(0) != sash, '上下列表高度可调整'

        with patch('git_archive.time.time', return_value=now):
            for label, hours in (('近 1 天', 24), ('近 2 天', 48)):
                button = next(button for button in picker.shortcut_buttons if button.cget('text') == label)
                button.invoke()
                wait_until(lambda: not picker.loading)
                expected = {sha for offset, sha in commits.items() if -hours * 3600 <= offset <= 0}
                assert {row.sha for row in picker.rows} == expected
                assert picker.recent_hours == hours and str(hours) in picker.time_range.get()
                assert set(picker.selected) == set(commits.values()), '时间过滤保留已选提交'
            picker.author.set('EXAMPLE.TEST')
            picker.filter_button.invoke()
            wait_until(lambda: not picker.loading and picker.preview_ok)
            assert picker.recent_hours == 48, '应用其他筛选应保留最近时间模式'
            picker.accept_button.invoke()
            assert accepted and accepted[0]['recent_hours'] == 48
            picker = gui.GitPicker(root, source, lambda *_: None, state=accepted[0])
            wait_until(lambda: not picker.loading)
            assert picker.recent_hours == 48 and set(picker.selected) == set(commits.values())
            picker.start_date.set('2026-10-08')
            picker.end_date.set('2026-10-08')
            picker.filter_button.invoke()
            wait_until(lambda: not picker.loading)
            assert picker.recent_hours == 0 and {row.sha for row in picker.rows} == {commits[0], commits[1]}
            picker.quick_date('近 1 天')
            wait_until(lambda: not picker.loading)
            picker.quick_date('不限')
            wait_until(lambda: not picker.loading)
            assert not picker.recent_hours and len(picker.rows) == len(commits)
    finally:
        picker.close()
        app.close()
        gc.collect()


if __name__ == '__main__':
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        run()
    finally:
        gc.collect()
        if gc_enabled:
            gc.enable()
