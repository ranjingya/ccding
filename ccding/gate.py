"""权限闸门：跟随 Claude 的真实权限请求。

依据 permission_mode + tool_name 还原「Claude 这次会不会弹权限」，只有会弹的才推送给你，
避免对不会被询问的工具（如只读工具、acceptEdits 下的编辑）造成打扰。

局限：读不到你 settings.json 里 permissions.allow 的永久白名单，已永久允许的工具可能仍被推送，
可用 config 的 always_allow（环境变量 CCDING_ALWAYS_ALLOW）手动补齐。
"""

from .config import Config
from .log import get_logger

logger = get_logger(__name__)

# Claude 不会弹权限询问的模式（直接放行，不打扰）：
#   bypassPermissions / dontAsk / auto —— 各种「不再询问/自动处理」语义；
#   plan —— 计划模式工具本就受限。
# 这几种模式下若仍插入远程授权，反而会无谓阻塞 Claude，故一律视为无需授权。
NO_PROMPT_MODES = {"bypassPermissions", "dontAsk", "auto", "plan"}


def need_approval(permission_mode: str, tool_name: str, config: Config) -> bool:
    """判断本次工具调用是否需要把授权请求推送给用户。

    功能说明：
        模拟 Claude Code 在不同 permission_mode 下是否会弹出权限询问：
          - 不会弹（bypassPermissions / plan / 自动接受的编辑 / 只读工具 / 已在白名单）→ 返回 False，钩子直接放行；
          - 会弹（需用户拍板）→ 返回 True，钩子接管并走远程授权流程。
        对未知模式 / 未知工具采取保守策略（视为需授权），宁可多发一条也不漏关键决策。
    参数：
        permission_mode: Claude 传入的权限模式，六种之一：
            default / plan / acceptEdits / auto / dontAsk / bypassPermissions。
        tool_name: 本次调用的工具名，如 Bash / Edit / WebFetch / mcp__xxx__yyy。
        config: 配置对象，提供 edit_tools / readonly_tools / always_allow 三个工具集合。
    返回值：
        True 表示需要授权（推送），False 表示无需授权（放行）。
    """
    mode = permission_mode or "default"

    # 用户永久白名单优先：无论何种模式都不打扰
    if tool_name in config.always_allow:
        logger.info("工具 %s 命中 always_allow 白名单 → 放行", tool_name)
        return False

    # 不会弹询问的模式：Claude 不会问，插入授权只会无谓阻塞
    if mode in NO_PROMPT_MODES:
        logger.info("模式 %s → 无需授权（tool=%s）", mode, tool_name)
        return False

    # 自动接受编辑模式：编辑族自动通过、只读工具本就不弹，其余（Bash/网络/MCP）仍需授权
    if mode == "acceptEdits":
        if tool_name in config.edit_tools:
            logger.info("模式 acceptEdits 且 %s 属编辑族 → 自动接受，无需授权", tool_name)
            return False
        if tool_name in config.readonly_tools:
            logger.info("模式 acceptEdits 且 %s 属只读工具 → 放行", tool_name)
            return False
        logger.info("模式 acceptEdits 且 %s 非编辑/只读 → 需授权", tool_name)
        return True

    # default 及未知模式：只读工具放行，其余（含编辑/Bash/网络/MCP）需授权
    if tool_name in config.readonly_tools:
        logger.info("模式 %s 且 %s 属只读工具 → 放行", mode, tool_name)
        return False

    logger.info("模式 %s 且 %s 非只读工具 → 需授权", mode, tool_name)
    return True
