"""python test_gui_controls.py；真实 Tk 日期与只读文件预览检查，不依赖 Git。"""

import tkinter as tk
from datetime import date

from zip_to_txt_gui import CalendarPopup, DateField, FilePreview, date_shortcut


def run():
    assert date_shortcut("当天", date(2024, 1, 3)) == ("2024-01-03", "2024-01-03")
    assert date_shortcut("昨天", date(2024, 1, 1)) == ("2023-12-31", "2023-12-31")
    assert date_shortcut("最近 7 天", date(2024, 1, 3)) == ("2023-12-28", "2024-01-03")
    assert date_shortcut("本月", date(2024, 2, 10)) == ("2024-02-01", "2024-02-29")
    assert date_shortcut("本月", date(2025, 2, 10)) == ("2025-02-01", "2025-02-28")
    assert date_shortcut("不限", date(2024, 1, 1)) == ("", "")
    root = tk.Tk()
    root.geometry("400x120")
    variable = tk.StringVar(value="2024-02-10")
    field = DateField(root, variable)
    field.pack(padx=20, pady=20)
    actions = []
    try:
        root.update()
        field.entry.focus_force()
        root.update()
        field.entry.event_generate("<Button-1>", x=5, y=5)
        root.update()
        popup = field.popup
        assert popup.window.winfo_exists() and 29 in popup.day_buttons
        assert root.focus_get() == field.entry, "日期输入框应保持键盘焦点"
        field.entry.delete(0, "end")
        for digit in "2025-01-10":
            field.entry.event_generate("<KeyPress>", keysym="minus" if digit == "-" else digit)
        root.update()
        assert variable.get() == "2025-01-10", "日历打开时仍能直接键盘输入"
        popup.day_buttons[29].invoke()
        root.update()
        assert variable.get() == "2024-02-29"
        field.entry.delete(0, "end")
        field.entry.insert(0, "2025-01-10")
        field.button.invoke()
        popup = field.popup
        popup.previous.invoke()
        assert (popup.year, popup.month) == (2024, 12)
        popup.next.invoke()
        assert (popup.year, popup.month) == (2025, 1)
        field.set_enabled(False)
        assert not popup.window.winfo_exists()
        field.set_enabled(True)
        variable.set("手动输入尚未完成")
        field.button.invoke()
        assert variable.get() == "手动输入尚未完成", "打开日历不能覆盖手动输入"
        field.popup.window.destroy()
        for value, button in (("0001-01-01", "previous"), ("9999-12-31", "next")):
            variable.set(value)
            popup = CalendarPopup(field, variable)
            assert str(getattr(popup, button).cget("state")) == "disabled"
            popup.window.destroy()
        preview = FilePreview(root, "资料", ["a.txt", "子目录/b.txt"], ["删除.txt"],
                              lambda: actions.append("accepted"), lambda: actions.append("returned"),
                              sizes={"a.txt": 1024, "子目录/b.txt": 0}, accept_text="确认文件清单")
        assert preview.total_size == 1024 and "2 个文件" in preview.summary
        assert len(preview.tree.get_children()) == 3
        preview.cancel()
        assert actions == ["returned"]
        preview = FilePreview(root, "资料", [], ["删除.txt"],
                              lambda: actions.append("empty"), lambda: None)
        assert str(preview.accept_button.cget("state")) == "disabled"
        preview.accept_button.invoke()
        preview.accept()
        assert actions == ["returned"] and preview.window.winfo_exists()
        preview.cancel()
    finally:
        root.destroy()


if __name__ == "__main__":
    run()
    print("真实日期输入、日历、快捷日期和文件预览检查通过")
