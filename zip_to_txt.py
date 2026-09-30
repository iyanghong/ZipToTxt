#!/usr/bin/env python3
"""把 ZIP 切片加密为 Base64 文本，并将其还原。"""

import argparse
import base64
import binascii
import getpass
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError as exc:
    raise SystemExit("缺少 cryptography；请先运行 python -m pip install cryptography") from exc


DEFAULT_PART_SIZE_MB = 50
MAX_PART_SIZE_MB = 200
TEXT_LIMIT = DEFAULT_PART_SIZE_MB * 1_000_000  # MB 按十进制字节计算
MAX_TEXT_LIMIT = MAX_PART_SIZE_MB * 1_000_000
HEADER = struct.Struct(">5s16s8sII")
MAGIC = b"ZTXT1"
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{kind}{number}" for kind in ("COM", "LPT") for number in range(1, 10)
}


def key_from_password(password: str, salt: bytes) -> bytes:
    if not password:
        raise ValueError("密码不能为空")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def output_parent(output_dir: str | Path | None, default: Path) -> Path:
    if output_dir is None:
        return default
    if isinstance(output_dir, str) and not output_dir.strip():
        raise ValueError("输出位置不能为空")
    parent = Path(output_dir).expanduser()
    if parent.is_symlink():
        raise ValueError(f"输出位置不能是符号链接：{parent}")
    parent = parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError(f"输出位置必须是目录：{parent}")
    return parent


def check_names(names: list[str]) -> None:
    """拒绝常见的跨平台文件名冲突。"""
    seen = set()
    for name in names:
        if (
            not name
            or name in {".", ".."}
            or name.endswith((" ", "."))
            or INVALID_NAME.search(name)
            or name.split(".")[0].upper() in DEVICE_NAMES
        ):
            raise ValueError(f"文件名无法跨平台安全还原：{name!r}")
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"同一目录中有跨平台重名项：{name!r}")
        seen.add(folded)


def make_zip(source: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True, strict_timestamps=False
    ) as archive:
        if source.is_file():
            check_names([source.name])
            archive.write(source, source.name)
            return
        check_names([source.name])

        def fail_walk(error: OSError) -> None:
            raise error

        for root, dirs, files in os.walk(source, onerror=fail_walk, followlinks=False):
            dirs.sort()
            files.sort()
            check_names(dirs + files)
            for name in dirs + files:
                item = Path(root) / name
                if item.is_symlink() or not (item.is_dir() or item.is_file()):
                    raise ValueError(f"不支持符号链接或特殊文件：{item}")
            relative = Path(root).relative_to(source.parent).as_posix()
            archive.writestr(relative + "/", b"")
            for name in files:
                item = Path(root) / name
                archive.write(item, item.relative_to(source.parent).as_posix())


def encrypt_path(
    path: str | Path, password: str, progress: Callable[[str], None] | None = None,
    output_dir: str | Path | None = None,
    part_size_mb: int = DEFAULT_PART_SIZE_MB,
) -> Path:
    if type(part_size_mb) is not int or not 1 <= part_size_mb <= MAX_PART_SIZE_MB:
        raise ValueError(f"每个 TXT 上限必须是 1 到 {MAX_PART_SIZE_MB} 之间的整数 MB")
    chunk_size = part_size_mb * 1_000_000 // 4 * 3 - HEADER.size - 16  # AES-GCM 标签占 16 字节
    if isinstance(path, str) and not path.strip():
        raise ValueError("路径不能为空")
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError(f"不支持符号链接：{source}")
    source = source.resolve(strict=True)
    if not (source.is_file() or source.is_dir()) or source == source.parent:
        raise ValueError("路径必须指向普通文件或非根目录")
    output = output_parent(output_dir, source.parent) / (source.name + ".txtparts")
    if output.exists():
        raise FileExistsError(f"目标目录已存在：{output}")
    salt = os.urandom(16)
    archive_id = os.urandom(8)
    cipher = AESGCM(key_from_password(password, salt))

    with tempfile.TemporaryDirectory(prefix=".ztxt-", dir=source.parent) as temporary:
        zip_path = Path(temporary) / "archive.zip"
        if progress:
            progress("正在压缩文件…")
        make_zip(source, zip_path)
        count = max(1, (zip_path.stat().st_size + chunk_size - 1) // chunk_size)
        if count > 999_999:
            raise ValueError("分片数量超过 999999")
        output.mkdir()
        try:
            if progress:
                progress("正在加密并生成 TXT 分片…")
            with zip_path.open("rb") as zipped:
                for index in range(1, count + 1):
                    header = HEADER.pack(MAGIC, salt, archive_id, index, count)
                    nonce = archive_id + struct.pack(">I", index)
                    chunk = zipped.read(chunk_size)
                    encrypted = header + cipher.encrypt(nonce, chunk, header)
                    (output / f"part-{index:06d}.txt").write_bytes(base64.b64encode(encrypted))
        except BaseException:
            shutil.rmtree(output)
            raise
    return output


def check_zip(archive: zipfile.ZipFile) -> None:
    children: dict[str, set[str]] = {}
    entries = set()
    for info in archive.infolist():
        name = info.filename.rstrip("/")
        parts = name.split("/")
        if not name or "\\" in name or any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"ZIP 包含不安全路径：{info.filename!r}")
        if name in entries:
            raise ValueError(f"ZIP 包含重复路径：{info.filename!r}")
        entries.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"ZIP 包含符号链接或特殊文件：{info.filename!r}")
        for depth, part in enumerate(parts):
            children.setdefault("/".join(parts[:depth]), set()).add(part)
    for names in children.values():
        check_names(list(names))


def decrypt_path(
    path: str | Path, password: str, progress: Callable[[str], None] | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    if isinstance(path, str) and not path.strip():
        raise ValueError("路径不能为空")
    parts_dir = Path(path).expanduser()
    if parts_dir.is_symlink():
        raise ValueError(f"不支持符号链接：{parts_dir}")
    parts_dir = parts_dir.resolve(strict=True)
    if not parts_dir.is_dir():
        raise ValueError("还原路径必须是文本分片所在的目录")
    output = output_parent(output_dir, parts_dir) / "restored"
    if output.exists():
        raise FileExistsError(f"目标目录已存在：{output}")
    parts = [item for item in parts_dir.iterdir() if item.suffix.lower() == ".txt"]
    if not parts or len(parts) > 999_999:
        raise ValueError("目录中没有一套有效的 TXT 分片")
    expected = {f"part-{index:06d}.txt" for index in range(1, len(parts) + 1)}
    if {item.name for item in parts} != expected or any(not item.is_file() or item.is_symlink() for item in parts):
        raise ValueError("TXT 分片混杂、缺失或命名错误")

    with tempfile.TemporaryDirectory(prefix=".ztxt-", dir=parts_dir) as temporary:
        zip_path = Path(temporary) / "archive.zip"
        identity = None
        cipher = None
        if progress:
            progress("正在校验并解密 TXT 分片…")
        with zip_path.open("wb") as zipped:
            for index in range(1, len(parts) + 1):
                part = parts_dir / f"part-{index:06d}.txt"
                with part.open("rb") as stream:
                    encoded = stream.read(MAX_TEXT_LIMIT + 2)
                # 兼容文本保存时附加的单个末尾 NUL；Base64 内容仍受大小限制。
                if encoded.endswith(b"\0"):
                    encoded = encoded[:-1]
                if len(encoded) > MAX_TEXT_LIMIT:
                    raise ValueError(f"TXT 分片超过支持的 {MAX_PART_SIZE_MB} MB：{part.name}")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except binascii.Error as exc:
                    raise ValueError(f"TXT 分片不是有效 Base64：{part.name}") from exc
                if len(data) < HEADER.size + 16:
                    raise ValueError(f"TXT 分片过短：{part.name}")
                header = data[: HEADER.size]
                magic, salt, archive_id, part_index, count = HEADER.unpack(header)
                if magic != MAGIC or part_index != index or count != len(parts):
                    raise ValueError(f"TXT 分片格式或顺序错误：{part.name}")
                if identity is None:
                    identity = (salt, archive_id)
                    cipher = AESGCM(key_from_password(password, salt))
                elif identity != (salt, archive_id):
                    raise ValueError("TXT 分片来自不同归档")
                try:
                    zipped.write(cipher.decrypt(archive_id + struct.pack(">I", index), data[HEADER.size :], header))
                except InvalidTag as exc:
                    raise ValueError(f"密码错误或分片已损坏：{part.name}") from exc

        with zipfile.ZipFile(zip_path) as archive:
            check_zip(archive)
            if progress:
                progress("正在解压 ZIP…")
            output.mkdir()
            try:
                archive.extractall(output)
            except BaseException:
                shutil.rmtree(output)
                raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="待压缩的文件或目录，或 TXT 分片所在目录")
    parser.add_argument("-o", "--output-dir", help="输出位置：在指定目录下新建 .txtparts 或 restored")
    parser.add_argument("--part-size-mb", type=int, default=DEFAULT_PART_SIZE_MB,
                        help=f"压缩时每个 TXT 的大小上限，1～{MAX_PART_SIZE_MB} MB（默认 {DEFAULT_PART_SIZE_MB}）")
    args = parser.parse_args()
    print("1. 压缩并加密\n2. 解密并解压缩")
    while (choice := input("请输入数字 1 或 2：").strip()) not in {"1", "2"}:
        print("请输入 1 或 2")
    path = args.path or input("请输入路径：").strip()
    try:
        if choice == "1":
            password = getpass.getpass("设置密码：")
            if password != getpass.getpass("再次输入密码："):
                raise ValueError("两次密码不一致")
            output = encrypt_path(path, password, output_dir=args.output_dir, part_size_mb=args.part_size_mb)
        else:
            output = decrypt_path(path, getpass.getpass("输入密码："), output_dir=args.output_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"完成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
