# 调整 Git 提交窗口并增加滚动时间筛选

Status: ready-for-human
Type: task

需求见 ../spec.md。

- [x] 上下可调整列表，紧凑筛选区及全文查看。
- [x] 最近 24／48 小时筛选和状态回填。
- [x] 真实 Git、Tk、长数据及时间边界回归。
- [x] 三平台 v0.4.1 本地打包、包内测试和发布包校验。
- [x] README 和测试记录。

## Comments

当前智能体实施；保留已有未提交的修改。

完成记录见 `docs/test-results-git-layout-2026-10-08.md`。macOS、Linux 的真实 Git 与 Tk 回归通过；Windows 使用固定 Git 数据运行包内筛选和真实控件测试，实际 EXE 可见窗口检查通过，未声称完成原生 Windows 验收。
