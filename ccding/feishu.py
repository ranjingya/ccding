"""飞书自定义应用通道：发交互卡片 + 长连接收按钮回调。

发送走 client.im.v1.message.create（msg_type=interactive，content 为卡片 JSON 字符串）；
回调走长连接 WebSocket，注册 card.action.trigger，handler 内按 req_id 匹配本次授权并把决策塞入队列。

无常驻进程：每次授权临时起一条长连接（守护线程），拿到决策或超时后由钩子进程退出顺带回收。
lark 内部日志级别压到 ERROR，叠加钩子里的 stdout→stderr 重定向，确保不污染钩子的纯净 JSON stdout。
"""

from __future__ import annotations

import json
import queue
import threading
import time

from . import cards
from .config import Config
from .constants import APPROVE, DENY
from .log import get_logger

logger = get_logger(__name__)

# 启动长连接后、发卡片前的建连等待（秒），确保点击事件不被漏接
WS_CONNECT_WAIT = 1.5


class FeishuChannel:
    """飞书通道：持有配置与惰性创建的 lark Client。"""

    def __init__(self, config: Config):
        self._config = config
        self._client = None  # 惰性创建，避免未配置时引入 lark 开销

    @property
    def ready(self) -> bool:
        """飞书通道是否可用：开关开启且三要素齐备（app_id/app_secret/receive_id）。"""
        return self._config.feishu_enabled and self._config.feishu_ready

    def _build_client(self):
        """惰性创建并缓存 lark Client（内部自动管理 tenant_access_token）。"""
        if self._client is None:
            import lark_oapi as lark

            self._client = (
                lark.Client.builder()
                .app_id(self._config.app_id)
                .app_secret(self._config.app_secret)
                .log_level(lark.LogLevel.ERROR)
                .build()
            )
        return self._client

    def send_card(self, card: dict) -> bool:
        """发送一张交互卡片到配置的接收人。

        功能说明：
            按 receive_id_type/receive_id 把卡片以 msg_type=interactive 发送；content 必须是
            JSON 字符串（此处对卡片 dict 做 json.dumps）。
        参数：
            card: 卡片 dict（见 cards.py 构造）。
        返回值：
            发送成功 True，失败/未就绪 False。
        """
        if not self.ready:
            logger.warning("飞书未就绪，跳过发送卡片")
            return False
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            client = self._build_client()
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(self._config.receive_id_type)  # 外层 builder
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(self._config.receive_id)  # 内层 builder
                    .msg_type("interactive")
                    .content(json.dumps(card))  # content 必须是字符串
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "发送卡片失败 code=%s msg=%s log_id=%s",
                    response.code,
                    response.msg,
                    response.get_log_id(),
                )
                return False
            logger.info("发送卡片成功 message_id=%s", getattr(response.data, "message_id", None))
            return True
        except Exception as exc:  # 网络/凭据等异常不致命
            logger.warning("发送卡片异常：%s", exc)
            return False

    def send_completion(self, title: str, body: str) -> bool:
        """发送任务完成通知卡片（无按钮）。"""
        return self.send_card(cards.build_completion_card(title, body))

    def request_approval(self, title: str, body: str, req_id: str, timeout: float) -> str | None:
        """发授权卡片并临时起长连接收点击，阻塞返回决策。

        功能说明：
            先在守护线程启动长连接 WS 并注册 card.action.trigger handler（按 req_id 匹配本次），
            待建连后发送授权卡片，主调用阻塞等待 handler 把决策塞入队列或超时。
        参数：
            title: 授权标题（项目名 + 工具名）。
            body: 授权正文（命令/参数摘要）。
            req_id: 本次请求唯一标识，用于回调匹配。
            timeout: 最长等待秒数。
        返回值：
            APPROVE / DENY；超时、未就绪或发送失败返回 None。
        """
        if not self.ready:
            return None

        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTrigger,
            P2CardActionTriggerResponse,
        )

        decision_q: queue.Queue = queue.Queue()

        def on_card_action(data: "P2CardActionTrigger") -> "P2CardActionTriggerResponse":
            """长连接收到按钮点击：匹配 req_id 后取决策入队，3 秒内返回 toast。"""
            value = getattr(data.event.action, "value", None) or {}
            if value.get("req_id") != req_id:
                # 非本次请求（理论上 Claude 串行不会出现），忽略
                return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已忽略"}})
            decision = value.get("decision")
            if decision in (APPROVE, DENY):
                decision_q.put(decision)
                content = "已同意" if decision == APPROVE else "已拒绝"
                logger.info(
                    "飞书收到点击 req_id=%s decision=%s open_id=%s",
                    req_id,
                    decision,
                    getattr(data.event.operator, "open_id", None),
                )
                # 同一回调里回写卡片：按钮区换成已同意/已拒绝（须在 ~3s 内返回）
                return P2CardActionTriggerResponse(
                    {
                        "toast": {
                            "type": "success" if decision == APPROVE else "info",
                            "content": content,
                        },
                        "card": {"type": "raw", "data": cards.build_resolved_card(title, body, decision)},
                    }
                )
            return P2CardActionTriggerResponse({"toast": {"type": "warning", "content": "未知操作"}})

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")  # ws 模式凭据留空
            .register_p2_card_action_trigger(on_card_action)
            .build()
        )

        def run_ws() -> None:
            try:
                # 位置参数顺序为 (app_id, app_secret, log_level, event_handler)，event_handler 必须用关键字
                ws_client = lark.ws.Client(
                    self._config.app_id,
                    self._config.app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.ERROR,
                )
                ws_client.start()  # 永久阻塞；无公开 stop()，靠守护线程随进程退出终止
            except Exception as exc:
                logger.warning("飞书长连接异常：%s", exc)

        # 截止时刻在建连等待之前就锁定，使飞书的监听窗口与编排层的总超时对齐
        # （否则建连的 1.5s 会把窗口整体后移，编排层已超时返回后到的点击会丢失）
        deadline = time.monotonic() + timeout
        threading.Thread(target=run_ws, name="ccding-lark-ws", daemon=True).start()
        time.sleep(WS_CONNECT_WAIT)  # 等长连接建好再发卡片，避免漏接点击

        if not self.send_card(cards.build_approval_card(title, body, req_id)):
            logger.warning("授权卡片发送失败，飞书渠道放弃本次授权")
            return None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.info("建连/发送已耗尽超时预算，飞书放弃 req_id=%s", req_id)
            return None
        logger.info("授权卡片已发送，等待点击（≤%.1fs）req_id=%s", remaining, req_id)
        try:
            return decision_q.get(timeout=remaining)
        except queue.Empty:
            logger.info("飞书授权超时未点击 req_id=%s", req_id)
            return None


def get_channel(config: Config) -> FeishuChannel:
    """工厂：根据配置创建飞书通道。"""
    return FeishuChannel(config)
