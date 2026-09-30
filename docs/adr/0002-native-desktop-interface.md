# 使用 Tkinter 提供桌面界面

桌面版保留原有 CLI 和归档格式，只增加一个调用相同归档函数的 Tkinter 入口。Tkinter 使用系统原生控件，不引入额外 GUI 库；任务放在工作线程中，窗口保持响应。构建机必须有带 Tcl/Tk 的 Python，打包后目标机器不必另装 Python 或 Tk。Windows、macOS、Linux 的图形程序分别在对应系统构建。
