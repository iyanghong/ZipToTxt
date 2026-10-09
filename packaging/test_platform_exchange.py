"""三平台包内代码互通检查；由 test_bundle.py 加载后执行。

设置 ZTXT_EXCHANGE_ROOT、ZTXT_EXCHANGE_PLATFORM 和 ZTXT_EXCHANGE_ACTION。
三个平台先各运行 write，再各运行 read，核对九组还原结果。
"""

import hashlib
import json
import os
from pathlib import Path

from zip_to_txt import decrypt_path, encrypt_path


def run():
    root = Path(os.environ["ZTXT_EXCHANGE_ROOT"])
    platform = os.environ["ZTXT_EXCHANGE_PLATFORM"]
    destination = root / platform
    destination.mkdir(parents=True, exist_ok=True)
    if os.environ["ZTXT_EXCHANGE_ACTION"] == "write":
        source = destination / "交换资料"
        (source / "空目录").mkdir(parents=True)
        (source / "中文文件.bin").write_bytes(os.urandom(2_300_000))
        (source / "说明.txt").write_text("Windows、macOS、Linux 互通检查\n", encoding="utf-8")
        (source / "零字节.txt").write_bytes(b"")
        expected = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in source.rglob("*") if p.is_file()}
        (destination / "expected.json").write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
        encrypt_path(source, "互通测试密码", output_dir=destination, part_size_mb=1)
        print(f"{platform}：已使用实际包内代码生成多片归档", flush=True)
        return
    for writer in ("macos", "linux", "windows"):
        incoming = root / writer
        output = destination / ("from-" + writer)
        output.mkdir()
        restored = decrypt_path(incoming / "交换资料.txtparts", "互通测试密码", output_dir=output) / "交换资料"
        expected = json.loads((incoming / "expected.json").read_text(encoding="utf-8"))
        actual = {p.relative_to(restored).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in restored.rglob("*") if p.is_file()}
        assert actual == expected and (restored / "空目录").is_dir()
        print(f"{writer} → {platform}：文件范围、SHA-256、零字节和空目录通过", flush=True)


if __name__ == "__main__":
    run()
