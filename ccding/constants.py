"""跨模块共享的决策常量。

授权决策在飞书回调、桌面回调、编排层之间传递，统一用这两个字面量，None 表示未点击（超时/不可用）。
"""

APPROVE = "approve"
DENY = "deny"

# 通知标题前缀：放在标题最前，让推送横幅/聊天列表一眼区分「需审批」与「已完成」
APPROVAL_TITLE_PREFIX = "🔴 需审批 · "
COMPLETION_TITLE_PREFIX = "✅ 已完成 · "
