"""桌面通知与本机授权弹窗。

抽象 Notifier，按平台提供 WindowsNotifier / MacNotifier / NullNotifier：
  - notify(title, body): 纯通知，无按钮，静音；
  - request_approval(title, body, timeout): 带「同意/拒绝」按钮，阻塞至点击或超时，返回决策。

作为飞书的本机并行兜底；与飞书「先点先生效」（见 approval.py）。失败一律降级为返回 None，不影响主流程。
"""

from __future__ import annotations

import subprocess
import threading
from abc import ABC, abstractmethod

from .config import Config
from .constants import APPROVE, DENY
from .log import get_logger
from .platform_detect import get_platform

logger = get_logger(__name__)


class Notifier(ABC):
    """桌面通知器抽象基类。"""

    @abstractmethod
    def notify(self, title: str, body: str) -> None:
        """发送一条无按钮的静音通知（完成提醒用）。"""

    @abstractmethod
    def request_approval(self, title: str, body: str, timeout: float) -> str | None:
        """弹出带「同意/拒绝」按钮的授权请求，阻塞等待。

        功能说明：
            在本机弹出可点击的授权窗口并阻塞，直到用户点击或超时。
        参数：
            title: 标题（项目名 + 工具名）。
            body: 正文（命令/参数摘要）。
            timeout: 最长等待秒数。
        返回值：
            APPROVE / DENY；超时或不可用返回 None。
        """


class NullNotifier(Notifier):
    """其它平台 / 不支持桌面通知时的空实现：仅记录日志。"""

    def notify(self, title: str, body: str) -> None:
        logger.info("[NullNotifier] 跳过桌面通知：%s | %s", title, body)

    def request_approval(self, title: str, body: str, timeout: float) -> str | None:
        logger.info("[NullNotifier] 不支持桌面授权，交由飞书处理：%s", title)
        return None


class WindowsNotifier(Notifier):
    """Windows toast 通知器（windows-toasts / WinRT）。

    授权 toast 的回调在 WinRT 运行时线程触发，故用 threading.Event 跨线程回传并保活等待。
    回调仅在进程存活期间有效——正好阻塞钩子在等待期间是存活的。
    """

    def __init__(self, aumid: str):
        self._aumid = aumid

    def notify(self, title: str, body: str) -> None:
        try:
            from windows_toasts import Toast, ToastAudio, WindowsToaster

            toaster = WindowsToaster(title or "ccding")
            toast = Toast([title, body])
            toast.audio = ToastAudio(silent=True)  # 静音
            toaster.show_toast(toast)
            logger.info("[Windows] 已弹出完成通知：%s", title)
        except Exception as exc:  # 通知失败不致命
            logger.warning("[Windows] 弹出通知失败：%s", exc)

    def request_approval(self, title: str, body: str, timeout: float) -> str | None:
        try:
            from windows_toasts import (
                InteractableWindowsToaster,
                Toast,
                ToastActivatedEventArgs,
                ToastAudio,
                ToastButton,
            )
        except Exception as exc:
            logger.warning("[Windows] 无法导入 windows-toasts，跳过桌面授权：%s", exc)
            return None

        # 第一个位置参数是显示名(applicationText)，AUMID 为关键字参数
        toaster = InteractableWindowsToaster(title or "ccding", notifierAUMID=self._aumid)
        toast = Toast([title, body])
        toast.audio = ToastAudio(silent=True)
        # content=可见文字，arguments=点击后回传到 event_args.arguments 的标识
        toast.AddAction(ToastButton("同意", f"ccding:{APPROVE}"))
        toast.AddAction(ToastButton("拒绝", f"ccding:{DENY}"))

        result: dict[str, str | None] = {"value": None}
        done = threading.Event()

        def on_activated(event_args: "ToastActivatedEventArgs") -> None:
            arg = (event_args.arguments or "") if event_args else ""
            if APPROVE in arg:
                result["value"] = APPROVE
            elif DENY in arg:
                result["value"] = DENY
            logger.info("[Windows] 收到 toast 点击：%s -> %s", arg, result["value"])
            done.set()  # 唤醒等待线程（回调在 WinRT 线程触发）

        toast.on_activated = on_activated
        try:
            toaster.show_toast(toast)  # 立即返回，不阻塞
            logger.info("[Windows] 已弹出授权 toast，等待点击（≤%ss）", timeout)
        except Exception as exc:
            logger.warning("[Windows] 弹出授权 toast 失败：%s", exc)
            return None

        done.wait(timeout=timeout)  # 阻塞等待点击或超时，期间进程保持存活以接收回调
        return result["value"]


def _applescript_quote(text: str) -> str:
    """把字符串转义为 AppleScript 字面量（处理反斜杠和双引号）。"""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run_osascript(script: str, timeout: float | None = None) -> tuple[int, str, str]:
    """运行一段 AppleScript（osascript 子进程）。

    返回 (returncode, stdout_stripped, stderr_stripped)。
    """
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


class MacNotifier(Notifier):
    """macOS 通知器：授权用 display dialog（阻塞返回按钮），完成用 display notification。

    display dialog 是模态对话框会抢焦点，但对非打包脚本可靠；display notification 在新版
    macOS 上可能被静默丢弃（取决于宿主 app 的通知权限），仅作尽力而为。
    """

    def notify(self, title: str, body: str) -> None:
        try:
            script = (
                f"display notification {_applescript_quote(body)} "
                f"with title {_applescript_quote(title)}"
            )
            _run_osascript(script, timeout=10)
            logger.info("[macOS] 已尝试发送通知（可能受宿主通知权限影响）：%s", title)
        except Exception as exc:
            logger.warning("[macOS] 发送通知失败：%s", exc)

    def request_approval(self, title: str, body: str, timeout: float) -> str | None:
        # 用「||」分隔 button returned 与 gave up，以区分真实点击与超时自动消失
        message = f"{title}\n\n{body}"
        script = (
            f"set r to (display dialog {_applescript_quote(message)} "
            f'buttons {{"拒绝", "同意"}} default button "同意" '
            f"giving up after {int(timeout)})\n"
            f'return (button returned of r) & "||" & (gave up of r)'
        )
        try:
            # 子进程墙钟超时给一点余量，避免 osascript 卡死
            code, out, err = _run_osascript(script, timeout=timeout + 10)
        except subprocess.TimeoutExpired:
            logger.warning("[macOS] 授权对话框子进程超时")
            return None
        except Exception as exc:
            logger.warning("[macOS] 授权对话框失败：%s", exc)
            return None

        if code != 0:
            # 通常是用户取消(-128)等；无 cancel button 时一般不会发生
            logger.info("[macOS] 对话框非正常返回 code=%s err=%s", code, err)
            return None

        button, _, gave_up = out.partition("||")
        if gave_up.strip().lower() == "true":
            logger.info("[macOS] 对话框超时自动消失（未点击）")
            return None
        decision = APPROVE if button.strip() == "同意" else DENY
        logger.info("[macOS] 用户点击：%s -> %s", button.strip(), decision)
        return decision


def get_notifier(config: Config) -> Notifier:
    """按当前平台返回桌面通知器实例。

    功能说明：
        启动自动识别系统，选择 WindowsNotifier / MacNotifier / NullNotifier。
    参数：
        config: 配置对象（Windows 需其中的 win_aumid）。
    返回值：
        对应平台的 Notifier 实例。
    """
    if not config.desktop_enabled:
        logger.info("桌面通知已关闭（CCDING_DESKTOP_ENABLED=false）→ 使用 NullNotifier")
        return NullNotifier()
    plat = get_platform()
    if plat == "win":
        logger.info("使用 WindowsNotifier（AUMID=%s）", config.win_aumid)
        return WindowsNotifier(config.win_aumid)
    if plat == "mac":
        logger.info("使用 MacNotifier")
        return MacNotifier()
    logger.info("使用 NullNotifier（平台=%s）", plat)
    return NullNotifier()
