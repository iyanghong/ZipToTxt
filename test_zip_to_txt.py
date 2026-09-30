"""运行方式：python test_zip_to_txt.py。"""

import base64
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import zip_to_txt


def expect_error(action, error=ValueError) -> None:
    try:
        action()
    except error:
        return
    raise AssertionError("预期操作报错")


def run() -> None:
    original_max_limit = zip_to_txt.MAX_TEXT_LIMIT
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "资料"
            (source / "空目录").mkdir(parents=True)
            (source / "子目录").mkdir()
            (source / "子目录" / "内容.txt").write_text("跨平台还原\n" * 100, encoding="utf-8")
            (source / "随机.bin").write_bytes(os.urandom(2_300_000))
            parts = zip_to_txt.encrypt_path(source, "测试密码", part_size_mb=1)
            assert len(list(parts.glob("*.txt"))) > 1
            assert max(part.stat().st_size for part in parts.glob("*.txt")) == 1_000_000
            expect_error(lambda: zip_to_txt.decrypt_path(parts, "错误密码"))
            assert not (parts / "restored").exists()
            restored = zip_to_txt.decrypt_path(parts, "测试密码")
            assert (restored / "资料" / "空目录").is_dir()
            assert (restored / "资料" / "子目录" / "内容.txt").read_bytes() == (source / "子目录" / "内容.txt").read_bytes()
            assert (restored / "资料" / "随机.bin").read_bytes() == (source / "随机.bin").read_bytes()

            destination = root / "指定输出位置"
            destination.mkdir()
            custom_parts = zip_to_txt.encrypt_path(source, "测试密码", output_dir=destination, part_size_mb=1)
            assert custom_parts == (destination / "资料.txtparts").resolve()
            expect_error(lambda: zip_to_txt.encrypt_path(source, "测试密码", output_dir=destination), FileExistsError)
            restore_parent = root / "指定还原位置"
            restore_parent.mkdir()
            expect_error(lambda: zip_to_txt.decrypt_path(custom_parts, "错误密码", output_dir=restore_parent))
            assert not (restore_parent / "restored").exists()
            custom_restored = zip_to_txt.decrypt_path(custom_parts, "测试密码", output_dir=restore_parent)
            assert custom_restored == (restore_parent / "restored").resolve()
            assert (custom_restored / "资料" / "子目录" / "内容.txt").read_bytes() == (source / "子目录" / "内容.txt").read_bytes()
            expect_error(lambda: zip_to_txt.decrypt_path(custom_parts, "测试密码", output_dir=restore_parent), FileExistsError)
            expect_error(lambda: zip_to_txt.encrypt_path(source, "测试密码", output_dir=source / "子目录" / "内容.txt"))
            shutil.rmtree(restored)
            (parts / "说明.txt").write_text("无关文本", encoding="utf-8")
            expect_error(lambda: zip_to_txt.decrypt_path(parts, "测试密码"))
            assert not (parts / "restored").exists()

            single = root / "单文件.bin"
            single.write_bytes(bytes(range(256)))
            files = zip_to_txt.encrypt_path(single, "测试密码")
            restored_file = zip_to_txt.decrypt_path(files, "测试密码")
            assert (restored_file / single.name).read_bytes() == single.read_bytes()
            shutil.rmtree(restored_file)
            first = files / "part-000001.txt"
            corrupted = bytearray(base64.b64decode(first.read_bytes()))
            corrupted[-1] ^= 1
            first.write_bytes(base64.b64encode(corrupted))
            expect_error(lambda: zip_to_txt.decrypt_path(files, "测试密码"))
            assert not (files / "restored").exists()
            first.unlink()
            expect_error(lambda: zip_to_txt.decrypt_path(files, "测试密码"))

            nul_source = root / "尾部空字节.bin"
            nul_source.write_bytes(os.urandom(2_300_000))
            nul_parts = zip_to_txt.encrypt_path(nul_source, "测试密码", part_size_mb=1)
            zip_to_txt.MAX_TEXT_LIMIT = max(part.stat().st_size for part in nul_parts.glob("*.txt"))
            for part in nul_parts.glob("*.txt"):
                part.write_bytes(part.read_bytes() + b"\0")
            nul_restored = zip_to_txt.decrypt_path(nul_parts, "测试密码")
            assert (nul_restored / nul_source.name).read_bytes() == nul_source.read_bytes()
            shutil.rmtree(nul_restored)
            third = nul_parts / "part-000003.txt"
            original_third = third.read_bytes()
            third.write_bytes((nul_parts / "part-000002.txt").read_bytes())
            expect_error(lambda: zip_to_txt.decrypt_path(nul_parts, "测试密码"))
            third.write_bytes(original_third)
            first = nul_parts / "part-000001.txt"
            terminated = first.read_bytes()
            first.write_bytes(terminated + b"\0")
            expect_error(lambda: zip_to_txt.decrypt_path(nul_parts, "测试密码"))
            first.write_bytes(terminated[:20] + b"\0" + terminated[21:])
            expect_error(lambda: zip_to_txt.decrypt_path(nul_parts, "测试密码"))
            assert not (nul_parts / "restored").exists()
            zip_to_txt.MAX_TEXT_LIMIT = original_max_limit

            large_source = root / "大分片.bin"
            large_source.write_bytes(os.urandom(40 * 1024 * 1024))
            default_parent = root / "默认上限"
            default_parent.mkdir()
            default_parts = zip_to_txt.encrypt_path(large_source, "测试密码", output_dir=default_parent)
            assert len(list(default_parts.glob("*.txt"))) == 2
            assert max(part.stat().st_size for part in default_parts.glob("*.txt")) == 50_000_000
            default_restored = zip_to_txt.decrypt_path(default_parts, "测试密码")
            with large_source.open("rb") as original, (default_restored / large_source.name).open("rb") as restored:
                assert hashlib.file_digest(original, "sha256").digest() == hashlib.file_digest(restored, "sha256").digest()
            large_parts = zip_to_txt.encrypt_path(large_source, "测试密码", part_size_mb=100)
            assert len(list(large_parts.glob("*.txt"))) == 1
            assert 50_000_000 < (large_parts / "part-000001.txt").stat().st_size <= 100_000_000
            large_restored = zip_to_txt.decrypt_path(large_parts, "测试密码")
            with large_source.open("rb") as original, (large_restored / large_source.name).open("rb") as restored:
                assert hashlib.file_digest(original, "sha256").digest() == hashlib.file_digest(restored, "sha256").digest()

            invalid_source = root / "非法大小.bin"
            invalid_source.write_bytes(b"test")
            for invalid in (0, 201, 1.5, True):
                expect_error(lambda: zip_to_txt.encrypt_path(invalid_source, "测试密码", part_size_mb=invalid))
            assert not (root / "非法大小.bin.txtparts").exists()
            assert zip_to_txt.encrypt_path(invalid_source, "测试密码", part_size_mb=200).is_dir()
    finally:
        zip_to_txt.MAX_TEXT_LIMIT = original_max_limit


if __name__ == "__main__":
    run()
    print("往返还原与错误检查通过")
