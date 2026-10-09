"""查询本地 Git 历史，用提交确定当前磁盘文件的范围。"""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Commit:
    sha: str
    timestamp: int
    author: str
    email: str
    subject: str


def git(source: Path, *args: str) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("未找到系统 Git，提交打包功能已禁用")
    environment = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        environment.pop(name, None)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            [executable, "--no-pager", "--literal-pathspecs", "-C", str(source), *args],
            capture_output=True, env=environment, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git 查询超时，请缩小范围后重试") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git 查询失败：{detail or '命令未成功执行'}")
    return result.stdout


def repository_for(path: str | Path) -> Path:
    source = Path(path).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError("请选择 Git 工作树内的普通目录")
    source = source.resolve(strict=True)
    root = Path(os.fsdecode(git(source, "rev-parse", "--show-toplevel")).rstrip("\r\n"))
    git(source, "rev-parse", "--verify", "HEAD^{commit}")
    return root.resolve(strict=True)


def branches_for(source: Path) -> tuple[dict[str, str], str]:
    rows = git(source, "for-each-ref", "--format=%(refname)\t%(symref)",
               "refs/heads", "refs/remotes").decode("utf-8").splitlines()
    branches = {}
    for row in rows:
        ref, symbolic = row.split("\t", 1)
        if symbolic:
            continue
        local = ref.startswith("refs/heads/")
        name = ref.removeprefix("refs/heads/" if local else "refs/remotes/")
        branches[("本地：" if local else "远程：") + name] = ref
    current = git(source, "rev-parse", "--symbolic-full-name", "HEAD").decode().strip()
    if current == "HEAD":
        branches = {"当前检出：HEAD": "HEAD", **branches}
    selected = next((label for label, ref in branches.items() if ref == current), "")
    return branches, selected


def date_bounds(start: str, end: str) -> tuple[float | None, float | None]:
    bounds = []
    for value in (start.strip(), end.strip()):
        if not value:
            bounds.append(None)
            continue
        try:
            day = datetime.strptime(value, "%Y-%m-%d")
            if day.strftime("%Y-%m-%d") != value:
                raise ValueError
        except ValueError as exc:
            raise ValueError("日期格式必须为 YYYY-MM-DD，例如 2026-10-07") from exc
        bounds.append(day)
    first, last = bounds
    if first and last and first > last:
        raise ValueError("开始日期不能晚于结束日期")
    return (first.timestamp() if first else None,
            (last + timedelta(days=1)).timestamp() if last else None)


def commits_for(source: Path, branch: str, start: str = "", end: str = "",
                author: str = "", *, recent_hours: int = 0) -> list[Commit]:
    first, last = date_bounds(start, end)
    if recent_hours not in (0, 24, 48):
        raise ValueError("最近时间范围必须为 24 或 48 小时")
    latest = time.time() if recent_hours else None
    if latest is not None:
        first, last = latest - recent_hours * 3600, None
    root = repository_for(source)
    scope = source.resolve().relative_to(root).as_posix()
    # ponytail: 读取本地提交元数据再筛选，百万级历史时改为分页查询。
    fields = git(root, "log", "--full-history", "-z", "--no-color", "--no-decorate",
                 "--format=%H%x00%ct%x00%an%x00%ae%x00%s", branch, "--", scope).split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 5:
        raise ValueError("无法读取 Git 提交信息")
    needle = author.strip().casefold()
    commits = []
    for index in range(0, len(fields), 5):
        sha, timestamp, name, email, subject = (x.decode("utf-8", errors="replace")
                                               for x in fields[index:index + 5])
        commit = Commit(sha, int(timestamp), name, email, subject)
        if first is not None and commit.timestamp < first:
            continue
        if last is not None and commit.timestamp >= last:
            continue
        if latest is not None and commit.timestamp > latest:
            continue
        if needle not in f"{name} <{email}>".casefold():
            continue
        commits.append(commit)
    return sorted(commits, key=lambda item: item.timestamp, reverse=True)


def changed_files(source: Path, commits: list[str] | tuple[str, ...]) -> list[str]:
    if not commits:
        raise ValueError("请至少选择一个提交")
    root = repository_for(source)
    source = source.resolve(strict=True)
    scope = source.relative_to(root)
    names = set()
    for sha in dict.fromkeys(commits):
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            raise ValueError("提交标识无效，请重新选择提交")
        parents = git(source, "rev-list", "--parents", "-n", "1", sha, "--").split()
        if len(parents) > 1:
            raw = git(root, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
                      parents[1].decode(), sha, "--", scope.as_posix())
        else:
            raw = git(root, "diff-tree", "--root", "--no-commit-id", "--no-ext-diff",
                      "--no-renames", "--name-only", "-r", "-z", sha, "--", scope.as_posix())
        for name in raw.split(b"\0"):
            if not name:
                continue
            relative = PurePosixPath(os.fsdecode(name))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Git 返回了目录范围之外的路径")
            try:
                scoped = relative.relative_to(PurePosixPath(scope.as_posix()))
            except ValueError as exc:
                raise ValueError("Git 返回了所选目录范围之外的路径") from exc
            names.add(scoped.as_posix())
    return sorted(names)
