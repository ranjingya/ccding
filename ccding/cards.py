"""飞书卡片 JSON 构造（schema 2.0）。

授权卡（含同意/拒绝回调按钮）、授权后更新卡（按钮换成已同意/已拒绝）、完成卡（无按钮）。
正文用 schema 2.0 的 markdown 组件渲染：命令放进代码块原样等宽展示，特殊字符不会破坏渲染。
按钮自定义值放在 behaviors.callback.value，点击后由长连接 handler 从 data.event.action.value 收到。
"""

from __future__ import annotations

from .constants import APPROVE, DENY
from .log import get_logger

logger = get_logger(__name__)

# 授权卡正文（命令/参数）最大长度；完成卡正文更短
MAX_BODY = 1500
MAX_COMPLETION = 240
# 授权卡输入框的 name，回调时按此名从 form_value 取「补充指示」文字
INSTRUCTION_FIELD = "instruction"


def _truncate(text: str, limit: int) -> str:
    """按长度截断，超长加省略标记。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…（已截断）"


def _code_block(text: str) -> str:
    """把文本包成 markdown 代码块，等宽原样展示（内部的 * _ ` 等不会被解析）。

    防御：内容若含三反引号会破坏围栏，替换为不显眼的等价字符。
    """
    text = (text or "").strip().replace("```", "ʼʼʼ")
    return f"```\n{text}\n```"


def _button(text: str, decision: str, req_id: str, btn_type: str) -> dict:
    """构造一个表单提交按钮，点击时连同输入框一起提交并回调。

    schema 2.0 表单提交按钮须有唯一 name + form_action_type="submit"（v1 的 action_type=form_submit 在 2.0 会校验失败）。
    behaviors.callback.value 透传 {req_id, decision} 回长连接 handler；width=fill 让按钮撑满所在列。
    """
    return {
        "tag": "button",
        "name": f"btn_{decision}",  # 同一表单内 name 须唯一
        "form_action_type": "submit",  # schema 2.0 表单提交按钮的正确字段
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "width": "fill",
        "behaviors": [{"type": "callback", "value": {"req_id": req_id, "decision": decision}}],
    }


def _button_row(req_id: str) -> dict:
    """把同意/拒绝两个按钮放进同一行（column_set 两列均分，手机端也不换行）。"""

    def _col(button: dict) -> dict:
        return {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "vertical_align": "center",
            "elements": [button],
        }

    return {
        "tag": "column_set",
        "flex_mode": "none",  # 窄屏按比例压缩、始终同一行（stretch 会变回竖排堆叠）
        "horizontal_spacing": "default",
        "columns": [
            _col(_button("同意", APPROVE, req_id, "primary")),
            _col(_button("拒绝", DENY, req_id, "danger")),
        ],
    }


def build_approval_card(title: str, body: str, req_id: str) -> dict:
    """构造授权请求卡片（schema 2.0，含同意/拒绝回调按钮）。

    功能说明：
        标题为「项目名 · 工具名」，正文为命令/参数摘要（代码块等宽展示、自动截断）；
        同意/拒绝两个按钮通过 behaviors.callback 回传 {req_id, decision}，点击后由长连接 handler
        按 req_id 匹配本次授权。config.update_multi=true 以便点击后能回写更新卡片。
    参数：
        title: 卡片标题。
        body: 命令/参数摘要（会截断）。
        req_id: 本次请求唯一标识，写入按钮回传值用于匹配。
    返回值：
        卡片 dict（发送前由调用方 json.dumps 序列化）。
    """
    logger.info("构造授权卡片：title=%s req_id=%s", title, req_id)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "orange"},
        "body": {
            "elements": [
                {"tag": "markdown", "content": _code_block(_truncate(body, MAX_BODY))},
                {
                    # 输入框与提交按钮须同处一个 form，点按钮时一起提交，回调才拿得到输入文字
                    "tag": "form",
                    "name": f"form_{req_id}",
                    "elements": [
                        # 按钮在上、补充指示在下；二者同处一个 form，点按钮即收输入，顺序不影响取值
                        _button_row(req_id),
                        {
                            "tag": "input",
                            "name": INSTRUCTION_FIELD,
                            "width": "fill",  # 撑满整行，否则默认窄宽、右边空着
                            "required": False,
                            "max_length": 1000,
                            # 不用单独的 label 占行，提示并入 placeholder
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "补充指示（可选）：拒绝时填这里告诉 Claude 怎么改",
                            },
                        },
                    ],
                },
            ]
        },
    }


def build_resolved_card(title: str, body: str, decision: str, instruction: str = "") -> dict:
    """构造授权后更新卡片（点击同意/拒绝后回写，按钮区换成状态文本）。

    功能说明：
        保留原标题与命令代码块，把两个按钮替换为「✅ 已同意 / ❌ 已拒绝」状态，并改头部配色，
        作为回调返回里的 card.data 整张回写（视觉上即只动了按钮区）。
    参数：
        title: 与授权卡相同的标题。
        body: 与授权卡相同的命令/参数摘要。
        decision: APPROVE 或 DENY。
        instruction: 用户在输入框填的补充指示，非空则附在状态下方展示。
    返回值：
        更新后的卡片 dict。
    """
    approved = decision == APPROVE
    status = "✅ 已同意" if approved else "❌ 已拒绝"
    template = "green" if approved else "red"
    status_md = f"**{status}**"
    if instruction:
        status_md += f"\n\n指示：{instruction}"
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "body": {
            "elements": [
                {"tag": "markdown", "content": _code_block(_truncate(body, MAX_BODY))},
                {"tag": "markdown", "content": status_md},
            ]
        },
    }


def build_completion_card(title: str, body: str) -> dict:
    """构造任务完成通知卡片（schema 2.0，无按钮，精简）。

    功能说明：
        标题为「项目名 · 已完成」，正文为最后一条助手消息的简短摘要（markdown 渲染、较短截断）。
    参数：
        title: 卡片标题。
        body: 完成摘要（会截断到较短长度）。
    返回值：
        卡片 dict。
    """
    logger.info("构造完成卡片：title=%s", title)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "green"},
        "body": {
            "elements": [
                {"tag": "markdown", "content": _truncate(body, MAX_COMPLETION) or "任务完成"},
            ]
        },
    }
