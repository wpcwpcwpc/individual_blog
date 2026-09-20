"""
QA Agent System — Frozen Runner

统一构造「子进程执行 python 代码/脚本」的 argv，兼容 dev 与 PyInstaller
frozen 两种环境。

背景：frozen 下 ``sys.executable`` 是可执行文件本身。``exe -c <script>``
不按 python 语义执行，而是把整个服务器再启动一遍 → 端口已被真服务器占用
→ exit=1。因此凡 ``create_subprocess_exec(sys.executable, "-c", ...)``
的调用点在打包环境必坏。既定通道是 ``main.py --run-script``（frozen 下
进程内 runpy，依赖已被打包进内部目录）。

用法::

    from core.frozen_runner import inline_code_argv, script_file_argv

    # inline 代码（dev: -c / frozen: 临时 .py + --run-script）
    with inline_code_argv(script) as argv:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    # 脚本文件（dev: python x.py / frozen: exe --run-script x.py）
    argv = script_file_argv(script_path, "--fs_id", fs_id)
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List


def _is_frozen() -> bool:
    """True 当运行在 PyInstaller 冻结环境（``sys.frozen`` 存在）。"""
    return bool(getattr(sys, "frozen", False))


def script_file_argv(script_path: str | Path, *args: str) -> List[str]:
    """构造执行脚本文件的子进程 argv（frozen 感知）。

    dev:    ``[python, script_path, *args]``
    frozen: ``[exe, "--run-script", script_path, *args]``
    """
    if _is_frozen():
        return [sys.executable, "--run-script", str(script_path), *args]
    return [sys.executable, str(script_path), *args]


@contextmanager
def inline_code_argv(code: str) -> Iterator[List[str]]:
    """构造执行 inline python 代码的子进程 argv（frozen 感知）。

    dev: ``[python, "-c", code]``；frozen: 代码落临时 .py 后走
    ``[exe, "--run-script", tmp]``（``--run-script`` 只收文件路径），
    退出 with 自动清理临时文件。
    """
    if not _is_frozen():
        yield [sys.executable, "-c", code]
        return
    fd, name = tempfile.mkstemp(suffix=".py", prefix="qa_agent_inline_")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(code, encoding="utf-8")
        yield [sys.executable, "--run-script", str(tmp)]
    finally:
        tmp.unlink(missing_ok=True)
