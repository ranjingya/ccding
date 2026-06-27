"""飞书卡片 JSON 构造。

授权卡（含同意/拒绝回调按钮）与完成卡（无按钮），均为飞书卡片 schema 2.0。
按钮自定义值放在 behaviors.callback.value，点击后由长连接 handler 从 data.event.action.value 收到。
"""

from __future__ import annotations

from .constants import APPROVE, DENY
from .log import get_logger

logger = get_logger(__name__)

# 卡片正文最大显示长度，超出截断（命令/消息可能很长）
MAX_BODY = 1500


def _truncate(text: str, limit: int = MAX_BODY) -> str:
    """正文截断，超长加省略标记。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…（已截断）"


def build_approval_card(title: str, body: str, req_id: str) -> dict:
    """构造授权请求卡片（schema 2.0，含同意/拒绝回调按钮）。

    功能说明：
        标题为「项目名 + 工具名」，正文为命令/参数摘要（自动截断）；同意/拒绝两个按钮通过
        behaviors 的 callback 回传 {req_id, decision}，点击后由飞书长连接 handler 收到并匹配 req_id。
    参数：
        title: 卡片标题。
        body: 卡片正文（会截断）。
        req_id: 本次请求唯一标识，写入按钮回传值用于匹配本次授权。
    返回值：
        卡片 dict（发送前由调用方 json.dumps 序列化为字符串）。
    """

    def _button(text: str, decision: str, btn_type: str) -> dict:
        return {
            "tag": "button",
            "text": {"tag": "plain_text", "content": text},
            "type": btn_type,
            "behaviors": [
                {"type": "callback", "value": {"req_id": req_id, "decision": decision}}
            ],
        }

    logger.info("构造授权卡片：title=%s req_id=%s", title, req_id)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "orange",
        },
        "body": {
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": _truncate(body)}},
                # schema 2.0 中按钮作为 body.elements 的直接元素
                _button("同意", APPROVE, "primary"),
                _button("拒绝", DENY, "danger"),
            ]
        },
    }


def build_completion_card(title: str, body: str) -> dict:
    """构造任务完成通知卡片（schema 2.0，无按钮）。

    功能说明：
        标题为项目名 + 完成提示，正文为最后一条助手消息摘要（自动截断）。
    参数：
        title: 卡片标题。
        body: 卡片正文（会截断）。
    返回值：
        卡片 dict（发送前由调用方 json.dumps 序列化为字符串）。
    """
    logger.info("构造完成卡片：title=%s", title)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "green",
        },
        "body": {
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": _truncate(body)}},
            ]
        },
    }
