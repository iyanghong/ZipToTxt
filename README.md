# ZIP 文本归档

把文件或目录压缩为 ZIP，用密码加密，再转换为按序编号的 Base64 文本分片。收齐分片后，可在 Windows、macOS 或 Linux 上用本程序还原。分片是加密文本，不能直接交给普通 ZIP 解压软件打开。

## 下载图形版

以下文件从 [v0.1.0 发布页](https://github.com/iyanghong/ZipToTxt/releases/tag/v0.1.0)下载：

| 系统 | 程序 | 运行方法 |
| --- | --- | --- |
| Windows x64 | [ZIP 包](https://github.com/iyanghong/ZipToTxt/releases/download/v0.1.0/zip-to-txt-windows-x86_64.zip) · [独立 EXE](https://github.com/iyanghong/ZipToTxt/releases/download/v0.1.0/ZipToTxt.exe) | 解压 ZIP 后双击 `ZipToTxt.exe`，或直接运行独立 EXE。 |
| macOS Apple 芯片 | [ZIP 包](https://github.com/iyanghong/ZipToTxt/releases/download/v0.1.0/zip-to-txt-macos-arm64.zip) | 解压后打开 `ZipToTxt.app`。 |
| Linux x64 | [tar.gz 包](https://github.com/iyanghong/ZipToTxt/releases/download/v0.1.0/zip-to-txt-linux-x86_64.tar.gz) | 解压后运行 `./zip-to-txt-gui`。 |

这些程序已包含 Python、Tkinter 和加密依赖，目标机器无需安装 Python。Linux 版在 Ubuntu 22.04 构建，需要兼容的图形桌面和系统库。macOS 版仅做本地临时签名，Windows 版未签名；系统首次打开时可能要求手动允许运行。Windows 版由 Wine 构建，尚未在原生 Windows 上完成运行测试。

[SHA-256 校验值](https://github.com/iyanghong/ZipToTxt/releases/download/v0.1.0/SHA256SUMS) · [当前版本实测记录](docs/test-results-2026-10-01.md)

## 图形界面使用

### 压缩并加密

1. 选择“压缩并加密”，再选择一个文件或目录。
2. “每个 TXT 上限”默认是 **50 MB**；可改为 **1～200 MB 的整数**。这里的 MB 按 1,000,000 字节计算，限制的是生成的 TXT 文件大小。
3. 输入密码并再次确认，点击“开始压缩并加密”。完成后，界面会显示输出目录，并可直接打开。

### 解密并解压缩

1. 将同一套 `part-000001.txt`、`part-000002.txt` 等文件放在一个目录中，不要混入其他 TXT，也不要改名或漏片。
2. 选择“解密并解压缩”，选择分片所在目录，输入压缩时的密码，点击“开始解密并解压缩”。无需填写原先设置的分片大小。
3. 完成后，在界面显示的输出目录中查找原始文件或目录。

两种操作都可以在“输出位置”中选择一个**已存在的目录**；留空则使用默认位置：

| 操作 | 留空时 | 指定输出位置 `D` 时 |
| --- | --- | --- |
| 压缩 | 在源文件或目录旁新建 `<原名称>.txtparts` | 在 `D` 下新建 `<原名称>.txtparts` |
| 还原 | 在分片目录内新建 `restored` | 在 `D` 下新建 `restored` |

例如压缩 `C:\work\资料`，默认得到 `C:\work\资料.txtparts`；还原这个分片目录，默认得到 `C:\work\资料.txtparts\restored\资料`。目标输出目录已存在时程序会报错，不会覆盖。

密码丢失、分片缺失或内容损坏时无法还原。单片上限设置得越大，处理时需要的内存越多。超过 50 MB 的分片须使用当前版本还原；旧版程序仍按 50 MB 拒绝。当前版本兼容 TXT 末尾意外附加的单个空字节（NUL），其余非法内容仍会报错。

## 从源码运行

需要 Python 3.10 或更新版本。先安装加密依赖：

```sh
python -m pip install cryptography
```

运行数字菜单命令行版：

```sh
python zip_to_txt.py "待处理路径"
python zip_to_txt.py "待处理路径" --part-size-mb 20 --output-dir "已有的输出目录"
```

启动后输入 `1` 压缩并加密，或输入 `2` 解密并解压缩。路径参数可省略，随后在菜单中输入。`--part-size-mb` 只用于压缩，默认 50；Windows 可把命令中的 `python` 换为 `py -3`。密码通过终端提示输入。

运行源码图形版还需 Python 支持 Tkinter：

```sh
python zip_to_txt_gui.py
```

可先用 `python -m tkinter` 检查 Tkinter。输入中的符号链接、跨平台禁用文件名以及同目录下仅大小写不同的名称会被拒绝。

## 自行构建图形版

在构建机器上安装带 Tkinter 的 Python，以及 `cryptography` 和 `pyinstaller`：

```sh
python -m pip install cryptography pyinstaller
```

分别在目标系统与架构上构建：

```sh
# Windows：dist/ZipToTxt.exe
python -m PyInstaller --onefile --windowed --name ZipToTxt zip_to_txt_gui.py

# macOS：dist/ZipToTxt.app
python -m PyInstaller --onedir --windowed --name ZipToTxt zip_to_txt_gui.py

# Linux：dist/ZipToTxt
python -m PyInstaller --onefile --name ZipToTxt zip_to_txt_gui.py
```

如需单独打包命令行版，在对应系统运行 `python -m PyInstaller --onefile --console --name zip-to-txt zip_to_txt.py`。

仓库的 `packaging/Dockerfile.linux` 可用于 Linux x64 构建，`packaging/Dockerfile.windows-wine` 可用于 Wine 交叉构建 Windows x64 程序。各平台产物不能互换；Windows 构建仍需在原生 Windows 上验证。[完整测试记录](docs/test-results-2026-10-01.md)。
