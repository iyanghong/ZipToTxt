"""python packaging/test_compatibility.py 新版本可执行文件 v0.3.0可执行文件。

读取两个真实产物内的归档代码，验证双方生成的分片可互相还原。
"""

import hashlib
import os
import sys
import tempfile
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def load_core(binary, name):
    archive = CArchiveReader(str(binary)).open_embedded_archive("PYZ.pyz")
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(archive.extract("zip_to_txt"), module.__dict__)
    return module


def run():
    current = load_core(sys.argv[1], "ztxt_current_core")
    previous = load_core(sys.argv[2], "ztxt_previous_core")
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        source = base / "兼容性测试"
        (source / "空目录").mkdir(parents=True)
        file = source / "中文文件.bin"
        file.write_bytes(os.urandom(2_300_000))
        expected = hashlib.sha256(file.read_bytes()).digest()
        for index, (writer, reader) in enumerate(((previous, current), (current, previous))):
            output = base / str(index)
            output.mkdir()
            parts = writer.encrypt_path(source, "跨版本密码", part_size_mb=1, output_dir=output)
            restored = reader.decrypt_path(parts, "跨版本密码") / source.name
            assert (restored / "空目录").is_dir()
            assert hashlib.sha256((restored / file.name).read_bytes()).digest() == expected
    print("v0.3.0 与当前产物双向分片还原通过")


if __name__ == "__main__":
    run()
