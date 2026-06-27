"""焦点闸门：判断运行本 Claude 会话的终端是否当前前台。

前台 → 不打扰（钩子把决策交回终端）；后台 → 才推送通知/授权。
启发式实现，非 100% 精确（多终端复用、tmux/SSH、WSL 等可能误判）；任何失败一律保守返回「后台」，
宁可多发一条，也不漏关键决策。
"""

from __future__ import annotations

import os
import subprocess

from .log import get_logger
from .platform_detect import get_platform

logger = get_logger(__name__)

# macOS 常见终端 / 宿主 app 进程名，用于与最前台 app 名比对
_MAC_TERMINAL_NAMES = {
    "Terminal",
    "iTerm2",
    "iTerm",
    "ghostty",
    "Ghostty",
    "WezTerm",
    "wezterm-gui",
    "Alacritty",
    "kitty",
    "Warp",
    "Code",  # VS Code 集成终端
    "Code - Insiders",
    "Cursor",
    "Hyper",
    "Tabby",
}


def _ancestor_pids() -> set[int]:
    """取当前钩子进程的全部祖先 PID（含自身）。"""
    pids: set[int] = {os.getpid()}
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        for parent in proc.parents():
            pids.add(parent.pid)
    except Exception as exc:
        logger.warning("获取祖先进程失败：%s", exc)
    return pids


def _ancestor_names() -> set[str]:
    """取当前钩子进程全部祖先的进程名（小写，去扩展名），用于与前台 app 名比对。"""
    names: set[str] = set()
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        for ancestor in [proc, *proc.parents()]:
            try:
                raw = ancestor.name()
            except Exception:
                continue
            if not raw:
                continue
            base = raw.lower()
            if base.endswith(".exe"):
                base = base[:-4]
            names.add(base)
    except Exception as exc:
        logger.warning("获取祖先进程名失败：%s", exc)
    return names


def _is_foreground_windows() -> bool:
    """Windows：前台窗口所属 PID 是否在钩子进程的祖先链中。"""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            logger.info("[Windows] 无前台窗口句柄，保守判为后台")
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        fg_pid = pid.value
        ancestors = _ancestor_pids()
        is_fg = fg_pid in ancestors
        logger.info("[Windows] 前台 PID=%s 祖先链=%s → 前台=%s", fg_pid, sorted(ancestors), is_fg)
        if is_fg:
            return True
        # 兜底：按进程名比对（前台进程名是否出现在祖先名集合中）
        try:
            import psutil

            fg_name = psutil.Process(fg_pid).name().lower()
            if fg_name.endswith(".exe"):
                fg_name = fg_name[:-4]
            if fg_name in _ancestor_names():
                logger.info("[Windows] 前台进程名 %s 命中祖先名集合 → 前台", fg_name)
                return True
        except Exception as exc:
            logger.warning("[Windows] 前台进程名比对失败：%s", exc)
        return False
    except Exception as exc:
        logger.warning("[Windows] 焦点检测失败，保守判为后台：%s", exc)
        return False


def _is_foreground_mac() -> bool:
    """macOS：最前台 app 名是否与钩子祖先终端名匹配。"""
    try:
        proc = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first process whose frontmost is true',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            logger.warning("[macOS] 取前台 app 失败（可能缺自动化权限）：%s", proc.stderr.strip())
            return False
        front = proc.stdout.strip()
        ancestor_names = _ancestor_names()
        # 前台 app 名在钩子祖先进程名集合中（说明最前台的就是跑本会话的终端）即判前台；
        # 或前台 app 是已知终端 app（兜底，祖先名未能识别出终端时仍可命中）。
        front_lower = front.lower()
        is_fg = front_lower in ancestor_names or front in _MAC_TERMINAL_NAMES
        logger.info("[macOS] 最前台 app=%s 祖先名=%s → 前台=%s", front, sorted(ancestor_names), is_fg)
        return is_fg
    except Exception as exc:
        logger.warning("[macOS] 焦点检测失败，保守判为后台：%s", exc)
        return False


def is_claude_foreground() -> bool:
    """判断当前 Claude 会话终端是否前台。

    功能说明：
        按平台分发到对应检测实现；任何异常或不支持的平台都保守返回 False（视为后台），
        以免漏发通知。
    参数：
        无。
    返回值：
        True 表示终端在前台（应不打扰）；False 表示后台或无法判定（应推送）。
    """
    plat = get_platform()
    if plat == "win":
        return _is_foreground_windows()
    if plat == "mac":
        return _is_foreground_mac()
    logger.info("平台 %s 不支持焦点检测，保守判为后台", plat)
    return False
