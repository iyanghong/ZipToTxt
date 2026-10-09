# Git 提交变更文件包测试记录

测试对象为本次源码改造和 `release/v0.2.0/` 下重新构建的三个平台产物。新包已在本地生成，尚未上传 GitHub Releases；既有 v0.1.0 下载包未改动。

| 产物 | 构建及测试环境 |
| --- | --- |
| macOS arm64 `.app` | macOS 26.5.2、Python 3.14.7、Tcl/Tk 9.0.4、cryptography 46.0.5、PyInstaller 6.22.3；本机原生运行 |
| Linux x86_64 可执行文件 | Ubuntu 22.04、Python 3.10.12、Tcl/Tk 8.6、cryptography 50.0.1、PyInstaller 6.22.3；Docker 的 x64 仿真及 Xvfb 图形环境 |
| Windows x86_64 `.exe` | Wine 构建、Windows Python 3.13.15、Tcl/Tk 8.6、cryptography 50.0.1、PyInstaller 6.22.3；Wine 9 和 Xvfb 中运行 |

## 执行命令

```sh
python3 test_zip_to_txt.py
python3 test_git_archive.py
/opt/homebrew/bin/python3.14 test_git_archive.py --gui
git diff --check
```

以上检查全部通过。

此外，通过 PyInstaller 的归档读取器加载每个平台可执行文件内的实际 `zip_to_txt`、`git_archive` 和 GUI 字节码，再运行归档回归测试。macOS、Linux 还运行了完整 Git 与真实 Tk 控件测试。对应的可复用入口是：

```sh
python packaging/test_bundle.py "可执行文件路径" test_zip_to_txt.py test_git_archive.py
```

测试环境的 Python 主、次版本须与构建版本一致，其他依赖由测试环境提供。这个检查验证包内代码，不能单独证明冻结启动器或所有附带运行库正确，因此也对三个实际可执行文件分别做了启动检查。

## 实际归档与还原

临时仓库使用中文和含方括号的子目录，创建初始提交、重复修改、重命名、删除、目录外修改和合并提交，并生成本地远程跟踪引用。随后修改磁盘文件但不提交，增加一个不在所选提交清单中的未跟踪文件。

所选提交的路径按预期去重，子目录筛选没有误匹配同级目录。新增、重命名、删除路径正确，合并提交只产生相对第一父提交的范围内文件。加密后实际解密并还原，文件集合和内容与当前磁盘逐字节核对一致，没有包含目录外文件、`.git` 或未选中的未跟踪文件。

日期筛选使用提交时间而非作者时间，作者姓名或邮箱支持包含匹配；无效日期和倒置日期范围报错。不安全相对路径、绝对路径、名称冲突、入口符号链接和目录内符号链接均被拒绝；预览后文件消失时未留下输出目录。原有完整目录、单文件、指定输出、分片大小、错误密码和损坏分片回归测试通过。

## 真实 Tk 控件与事件循环

测试创建实际 Tk 窗口和提交表格，调用界面操作并持续驱动 Tk 事件循环，检查控件状态、选择集合、弹窗结果和归档内容：

- 日期或作者筛选保留隐藏的已选提交，数量提示正确；切换分支清空选择。
- 文件预览区分当前可打包和已缺失路径；取消不产生文件，确认后完成加密并实际还原；纯删除提交不生成空归档。
- 真实生成 205 次文件修改提交，列表先显示 200 条，加载更多后显示 205 条，原有选择保留。
- 人为延长 Git 查询期间，Tk 定时回调仍正常执行，证明查询没有阻塞主事件循环。
- 模拟未找到系统 Git，提交按钮禁用；随后在普通目录完成加密和还原，原有功能可用。

## 验证范围

macOS 和 Linux 的包内应用代码均通过原有归档回归、Git 查询和真实 Tk 控件测试；实际冻结程序启动后保持运行，测试主动终止进程，未出现 Python 异常。macOS 的实际 `.app` 使用自身附带运行库启动。

Windows 在 Wine 9 中使用桌面启动方式 `wine start /unix .../ZipToTxt.exe`，独立启动真实 EXE；随后用 `packaging/test_frozen_windows.py` 枚举窗口，确认“ZIP 文本归档”主窗口已出现且没有错误弹窗。包内代码通过归档回归测试，以及实际 Windows Tk 控件的无 Git 禁用、普通压缩和还原检查。Windows 环境未安装 Git，因此未在 Windows 中验证 Git 子进程查询和完整提交选择流程；这些流程已在 macOS 和 Linux 中验证。

### Windows 启动诊断

最初使用 Linux 后台 shell 的 `wine ...exe >log 2>&1 &` 启动时，出现“Failed to start embedded python interpreter”。进一步用相同包内载荷的诊断启动器捕获到 `init_sys_streams`、`OSError: [WinError 6] Invalid handle`。自动隐藏控制台的测试构建也未解决这一后台启动方式的问题。

改用 Wine 的桌面启动方式后，原始 `--windowed` EXE 打开主窗口；再次使用最终交付的 EXE 重复该检查，并运行包内代码回归测试，全部通过。因此最终保留 `--windowed` 构建，没有在应用代码中加入诊断代码或控制台规避逻辑。这是本次 Wine 启动环境的标准流兼容问题，不能据此判断原生 Windows 的相同后台调用行为。

### 压缩包与完整性

三份最终压缩包均实际解包，解包后的可执行文件 SHA-256 与原始产物一致；Windows ZIP 的 CRC 检查通过，Linux 解包后的执行权限保留，macOS 解包后的 `.app` 通过 `codesign --verify --deep --strict` 验证临时签名。`SHA256SUMS` 中三个压缩包及独立 Windows EXE 的校验值均重新核对通过。

文件类型分别核对为 Windows PE32+ GUI x86-64、macOS Mach-O arm64、Linux ELF x86-64。三个包均包含本次新增 Git 模块，`release/` 保持 Git 忽略。

### 尚未覆盖

未在原生 Windows 或真实 Linux 桌面中进行点击验收。macOS 尝试做桌面截图检查时机器处于锁屏状态，未完成截图布局验收。Wine、Xvfb 和包内代码测试的通过结果不等同于这两项原生桌面验收。
