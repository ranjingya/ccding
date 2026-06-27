"""一次性脚本：拿到你的 open_id（用作 FEISHU_RECEIVE_ID）。

原理：临时起飞书长连接，监听你给机器人发的第一条消息，打印发送者 open_id 后退出。

前提：
    - .env 已填 FEISHU_APP_ID / FEISHU_APP_SECRET；
    - 飞书开发者后台已为该应用订阅「接收消息 im.message.receive_v1」事件，并选择「长连接」方式；
    - 应用已发布、机器人对你可见。
拿到 open_id 后可取消订阅该事件（仅授权回调 card.action.trigger 是常驻所需）。

用法：
    uv run python -m ccding.scripts.setup_feishu
然后在飞书里给机器人发任意一条消息。
"""

from __future__ import annotations

import os
import sys

from ..config import load_config
from ..log import get_logger

logger = get_logger(__name__)


def main() -> int:
    """启动长连接，捕获第一条消息并打印发送者 open_id。"""
    config = load_config()
    if not (config.app_id and config.app_secret):
        print("❌ 请先在 .env 配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET", file=sys.stderr)
        return 1

    import lark_oapi as lark

    print("📡 已连接，请在飞书里给你的机器人发送任意一条消息……（Ctrl+C 退出）")

    def on_message(data) -> None:
        """收到消息：打印发送者 open_id 并结束进程。"""
        open_id = None
        try:
            open_id = data.event.sender.sender_id.open_id
        except Exception as exc:
            logger.warning("解析发送者 open_id 失败：%s", exc)

        if open_id:
            print(f"\n✅ 你的 open_id 是：{open_id}")
            print("   填入 .env：FEISHU_RECEIVE_ID=该值，FEISHU_RECEIVE_ID_TYPE=open_id")
        else:
            print("\n⚠️ 收到消息但未能解析 open_id，请检查事件订阅配置。", file=sys.stderr)
        # 长连接 start() 无公开 stop()，直接结束进程
        os._exit(0)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = lark.ws.Client(
        config.app_id,
        config.app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.ERROR,
    )
    client.start()  # 阻塞直到收到消息后 os._exit
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
