"""Stop 钩子入口：任务完成通知（不阻塞、无决策输出）。

流程：
    1. 焦点闸门：Claude 终端在前台 → 直接退出，不打扰；
    2. 取正文：优先用 stdin 直接提供的 last_assistant_message，缺失才回退解析 transcript；
    3. 飞书发完成卡片 + 桌面纯通知（无按钮），退出。

本钩子不向 stdout 输出决策；仍重定向 stdout→stderr，防止 lark 等库的杂散输出影响调用方。
"""

from __future__ import annotations

import json
import sys

# 完成正文清洗参数
_MAX_LINES = 5
_MAX_CHARS = 1500


def _read_stdin_utf8() -> str:
    """读取 stdin 并按 UTF-8 解码。

    Claude 总是发 UTF-8 JSON；而本机（如中文 Windows）的 sys.stdin 默认用区域编码（GBK），
    直接 read() 会把 last_assistant_message / cwd 中的中文读成乱码（并可能令飞书发送因代理字符失败）。
    故显式走 buffer 按 UTF-8 解码；无 buffer 的流（测试）退回文本读取。
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8", errors="replace")
    return sys.stdin.read()


def _extract_last_assistant_text(transcript_path: str) -> str:
    """回退方案：从 transcript JSONL 提取最后一条助手文本。

    功能说明：
        仅当 stdin 未直接提供 last_assistant_message 时使用。message.content 是异构块数组
        （text/thinking/tool_use），跳过非 text 块并拼接 text 块，返回最后一条 assistant 的文本。
    参数：
        transcript_path: transcript.jsonl 路径。
    返回值：
        最后一条助手文本；读不到返回空串。
    """
    last_text = ""
    if not transcript_path:
        return last_text
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "assistant":
                    continue
                content = (evt.get("message") or {}).get("content", [])
                texts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if texts:
                    last_text = "".join(texts)
    except OSError as exc:
        # 这里 logger 已配置，但为避免在函数内反复取 logger，调用方记录足够
        sys.stderr.write(f"读取 transcript 失败：{exc}\n")
    return last_text


def _clean_message(text: str) -> str:
    """清洗助手消息为通知正文：去空行与 # 标题行，取前若干行并截断。"""
    lines = [
        line for line in (text or "").splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    joined = " ".join(lines[:_MAX_LINES]).strip()
    return joined[:_MAX_CHARS] or "任务完成"


def main() -> int:
    """钩子主流程。返回进程退出码（恒为 0）。"""
    # stdout 保护（本钩子无决策输出，但 lark 发送可能有杂散输出）
    sys.stdout = sys.stderr

    from ccding.log import get_logger

    logger = get_logger("ccding.stop")

    try:
        raw = _read_stdin_utf8()
        data = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        logger.warning("解析 stdin 失败：%s", exc)
        return 0

    logger.info("Stop 触发 stop_reason=%s", data.get("stop_reason"))

    # 1) 焦点闸门
    from ccding.focus import is_claude_foreground

    if is_claude_foreground():
        logger.info("Claude 终端在前台，完成通知略过")
        return 0

    from ccding.config import detect_project_name, load_config

    config = load_config()

    # 2) 取正文：优先直接字段，缺失回退 transcript
    last_msg = data.get("last_assistant_message")
    if not last_msg:
        last_msg = _extract_last_assistant_text(data.get("transcript_path", ""))
    body = _clean_message(last_msg)
    project = detect_project_name(data.get("cwd"))
    title = f"{project} · 已完成"
    logger.info("完成通知 title=%s 正文长度=%d", title, len(body))

    # 3) 飞书完成卡片 + 桌面纯通知
    from ccding.desktop import get_notifier
    from ccding.feishu import get_channel

    channel = get_channel(config)
    if channel.ready:
        channel.send_completion(title, body)
    else:
        logger.warning("飞书未配置，跳过完成卡片")

    try:
        get_notifier(config).notify(title, body)
    except Exception as exc:
        logger.warning("桌面通知失败：%s", exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
