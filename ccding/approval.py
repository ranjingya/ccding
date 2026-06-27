"""授权编排：飞书与桌面并行，先点先赢，超时回落。

PreToolUse 钩子在后台且需授权时调用本模块：同时起飞书卡片授权与本机桌面授权两条线程，
任一先返回有效决策即采用并立即返回；两条都未点击则超时返回 None（钩子据此回落到终端）。

无需显式取消败者：钩子拿到决策后即打印 JSON 并退出，进程退出会回收未结束的守护线程与飞书长连接。
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Protocol

from .desktop import APPROVE, DENY, Notifier
from .log import get_logger

logger = get_logger(__name__)


class FeishuChannel(Protocol):
    """approval 依赖的飞书渠道接口（由 feishu.py 实现）。"""

    ready: bool

    def request_approval(self, title: str, body: str, req_id: str, timeout: float) -> str | None:
        ...


def request_approval(
    notifier: Notifier | None,
    feishu_channel: FeishuChannel | None,
    title: str,
    body: str,
    req_id: str,
    timeout: float,
) -> str | None:
    """并行向飞书与桌面发起授权请求，返回最先到达的决策。

    功能说明：
        为飞书与桌面各起一个守护线程阻塞等待用户点击，结果投入线程安全队列；主线程在总超时内
        取第一个有效决策（APPROVE/DENY）即返回；某渠道提前返回 None（不可用/放弃）不影响继续等待
        其它渠道；全部未决则超时返回 None。
    参数：
        notifier: 桌面通知器（可为 None / NullNotifier）。
        feishu_channel: 飞书渠道（需 .ready 为真才参与）。
        title: 授权标题（项目名 + 工具名）。
        body: 授权正文（命令/参数摘要）。
        req_id: 本次请求唯一标识，用于飞书卡片回调匹配。
        timeout: 总等待上限秒数。
    返回值：
        APPROVE / DENY；超时或无可用渠道返回 None。
    """
    result_queue: "queue.Queue[tuple[str | None, str]]" = queue.Queue()
    workers: list[tuple[str, Callable[[], str | None]]] = []

    if feishu_channel is not None and getattr(feishu_channel, "ready", False):
        workers.append(
            ("feishu", lambda: feishu_channel.request_approval(title, body, req_id, timeout))
        )
    if notifier is not None:
        workers.append(("desktop", lambda: notifier.request_approval(title, body, timeout)))

    if not workers:
        logger.warning("无可用授权渠道（飞书未就绪且无桌面通知器）→ 直接超时回落")
        return None

    def run(name: str, func: Callable[[], str | None]) -> None:
        try:
            decision = func()
        except Exception as exc:  # 单渠道异常不拖垮整体
            logger.warning("[%s] 授权渠道异常：%s", name, exc)
            decision = None
        result_queue.put((decision if decision in (APPROVE, DENY) else None, name))

    for name, func in workers:
        threading.Thread(target=run, args=(name, func), daemon=True).start()
    logger.info("已并行发起授权：渠道=%s 超时=%ss req_id=%s", [w[0] for w in workers], timeout, req_id)

    pending = len(workers)
    deadline = time.monotonic() + timeout
    while pending > 0:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            decision, via = result_queue.get(timeout=remaining)
        except queue.Empty:
            break
        pending -= 1
        if decision in (APPROVE, DENY):
            logger.info("授权结果 via %s：%s", via, decision)
            return decision
        logger.info("[%s] 渠道未决/不可用，继续等待其它渠道（剩余 %s 个）", via, pending)

    logger.info("授权超时或所有渠道未决 → 返回 None（回落终端）")
    return None
