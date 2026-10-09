"""用与构建时相同的 Python 版本，测试产物内实际打包的应用代码。

python packaging/test_bundle.py 可执行文件 test_zip_to_txt.py test_git_archive.py
Git 测试包含真实 Tk 控件检查，需要系统 Git 和图形桌面。
"""

import gc
import marshal
import runpy
import sys
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def main():
    binary = Path(sys.argv[1])
    tests = sys.argv[2:]
    archive = CArchiveReader(str(binary))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    for name in ("zip_to_txt", "git_archive", "zip_to_txt_gui"):
        module = types.ModuleType(name)
        module.__file__ = str(binary.parent / (name + ".py"))
        sys.modules[name] = module
        code = (marshal.loads(archive.extract(name)) if name == "zip_to_txt_gui"
                else pyz.extract(name))
        source = Path(__file__).resolve().parents[1] / (name + ".py")
        assert code == compile(source.read_text(encoding="utf-8"), code.co_filename, "exec"), \
            f"产物内 {name} 与当前工作区源码不一致，请重新构建"
        exec(code, module.__dict__)
    print("已加载产物内的 GUI、归档和 Git 模块", flush=True)
    # 测试以 update() 驱动多个 Tk 窗口，在主线程回收；实际程序使用单个 mainloop()。
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for test in tests:
            sys.argv = [test, "--gui"] if Path(test).name == "test_git_archive.py" else [test]
            runpy.run_path(test, run_name="__main__")
            gc.collect()
    finally:
        if gc_enabled:
            gc.enable()


if __name__ == "__main__":
    main()
