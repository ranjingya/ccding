"""PreToolUse 钩子入口：交互式远程授权。

流程：
    1. 读 stdin(JSON)，解析 tool_name / permission_mode / tool_input / cwd；
    2. 权限闸门：这次 Claude 会弹权限吗？不会 → 不输出（正常放行）退出；
    3. 焦点闸门：Claude 终端在前台？是 → 输出 ask（交回终端，不打扰）退出；
    4. 后台且需授权 → 并行飞书卡片 + 桌面弹窗，先点先生效；
    5. 输出 permissionDecision=allow/deny；超时 → ask 回落终端。

stdout 契约：仅最终决策的纯净 JSON。处理期间把 sys.stdout 重定向到 stderr，最终 JSON 写回真正的
stdout 并用 ensure_ascii 保证纯 ASCII，杜绝任何库的杂散输出或编码问题污染契约。
"""

from __future__ import annotations

import json
import sys
import uuid


def _read_stdin_utf8() -> str:
    """读取 stdin 并按 UTF-8 解码。

    Claude 总是发 UTF-8 JSON；而本机（如中文 Windows）的 sys.stdin 默认用区域编码（GBK），
    直接 read() 会把中文命令/路径读坏甚至抛 UnicodeDecodeError，导致钩子放弃决策、绕过授权闸门。
    故显式走 buffer 按 UTF-8 解码；测试用 StringIO 等无 buffer 的流则退回文本读取。
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8", errors="replace")
    return sys.stdin.read()


def _decision(decision: str, reason: str, additional_context: str | None = None) -> dict:
    """构造 PreToolUse 决策输出 JSON。

    参数：
        decision: allow / deny / ask（defer 用「不输出」表达）。
        reason: 决策理由，回灌给 Claude（deny 时即「被拦原因/改法指示」）。
        additional_context: 可选附加上下文，注入给 Claude（allow 时携带用户补充指示）。
    返回值：符合钩子契约的 dict。
    """
    out: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    if additional_context:
        out["hookSpecificOutput"]["additionalContext"] = additional_context
    return out


def _build_title_body(data: dict, project: str) -> tuple[str, str]:
    """从工具调用生成通知标题与正文摘要（通用，不针对特定工具写死）。

    参数：
        data: 钩子 stdin 解析后的 dict。
        project: 项目名。
    返回值：
        (title, body)。title 为「项目 · 工具」，body 为命令/路径/参数摘要。
    """
    from ccding.constants import APPROVAL_TITLE_PREFIX

    tool = data.get("tool_name", "?")
    tool_input = data.get("tool_input", {}) or {}
    title = f"{APPROVAL_TITLE_PREFIX}{project} · {tool}"

    if tool == "Bash":
        body = tool_input.get("command", "")
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        body = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    elif tool in ("WebFetch", "WebSearch"):
        body = tool_input.get("url") or tool_input.get("query") or ""
    else:
        body = ""

    if not body:
        # 兜底：把整个 tool_input 序列化作摘要
        try:
            body = json.dumps(tool_input, ensure_ascii=False)
        except Exception:
            body = str(tool_input)
    return title, body


def main() -> int:
    """钩子主流程。返回进程退出码（恒为 0，JSON 仅在 exit 0 时被处理）。"""
    # stdout 保护：处理期间任何 print 都改道 stderr，最终决策才写回真 stdout
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    from ccding.log import get_logger

    logger = get_logger("ccding.pretooluse")

    def emit(payload: dict | None) -> None:
        """把决策 JSON（或空）写回真正的 stdout。"""
        if payload is not None:
            real_stdout.write(json.dumps(payload, ensure_ascii=True))
        real_stdout.flush()

    # 1) 读取并解析 stdin
    try:
        raw = _read_stdin_utf8()
        data = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        logger.warning("解析 stdin 失败，放行：%s", exc)
        emit(None)
        return 0

    tool_name = data.get("tool_name", "")
    permission_mode = data.get("permission_mode", "default")
    logger.info("PreToolUse 触发 tool=%s mode=%s", tool_name, permission_mode)

    from ccding.config import detect_project_name, load_config
    from ccding.gate import need_approval

    config = load_config()

    # 2) 权限闸门
    if not need_approval(permission_mode, tool_name, config):
        logger.info("无需授权，放行（不输出）tool=%s", tool_name)
        emit(None)
        return 0

    # 3) 焦点闸门：前台 → 交回终端
    from ccding.focus import is_claude_foreground

    if is_claude_foreground():
        logger.info("Claude 终端在前台，交回终端 ask tool=%s", tool_name)
        emit(_decision("ask", "Claude 窗口在前台，交回终端处理"))
        return 0

    # 4) 后台授权：并行飞书 + 桌面
    from ccding.approval import request_approval
    from ccding.constants import APPROVE, DENY
    from ccding.desktop import get_notifier
    from ccding.feishu import get_channel

    project = detect_project_name(data.get("cwd"))
    title, body = _build_title_body(data, project)
    req_id = uuid.uuid4().hex

    notifier = get_notifier(config)
    channel = get_channel(config)
    if not channel.ready:
        logger.warning("飞书未配置，本次仅桌面授权（或无可用渠道）")

    decision, note = request_approval(notifier, channel, title, body, req_id, config.approval_timeout)

    # 5) 输出决策（note 为用户在卡片输入框填的补充指示，可空）
    if decision == APPROVE:
        logger.info("远程同意 → allow（指示=%r）", note)
        # 同意时若带指示，作为附加上下文注入给 Claude
        emit(_decision("allow", "已远程同意", additional_context=(note or None)))
    elif decision == DENY:
        logger.info("远程拒绝 → deny（指示=%r）", note)
        # 拒绝时把用户指示作为「被拦原因/改法」回灌；没填则给通用理由
        emit(_decision("deny", note or "用户在飞书拒绝了该操作"))
    else:
        logger.info("远程授权超时 → ask 回落终端")
        emit(_decision("ask", "远程授权超时，交回终端处理"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
