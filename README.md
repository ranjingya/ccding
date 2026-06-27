# ccding — Claude Code 远程授权与完成提醒

> **ccding** = Claude Code 的「叮」一声。承接旧仓库 [ccdd](https://github.com/2234839/ccdd)（Claude Code 滴滴）的拟声含义，用 Python + uv 重写。

Claude Code 在后台跑时，把**需要你拍板的权限请求**推到飞书，你在手机上点「同意 / 拒绝」即可直接放行或拦截；任务**完成**时也发一条通知。不用一直盯着终端。

## 和旧版 ccdd 的区别

| | 旧版 ccdd（Node.js） | ccding（本项目） |
|---|---|---|
| 飞书接入 | 自定义机器人 **Webhook**，单向通知 | 自定义**应用** + 长连接，**可点按钮**交互 |
| 权限请求 | 不支持，只能回电脑点终端 | **手机点按钮直接授权**，决策回灌给 Claude |
| 通道 | 飞书 / Telegram / Windows 声音 | **仅飞书** + 本机桌面弹窗兜底 |
| 声音 | 有 | **无**（仅通知） |
| 平台 | 主要 Windows | Windows + macOS，启动自动识别 |

## 核心设计

- **无常驻进程**：钩子是短命进程，授权等待期间（≤600s）临时建一条飞书长连接 + 弹窗，点了就返回退出。无后台服务、无开机自启。
- **跟随真实权限请求**：用 `permission_mode` + 工具类别还原「Claude 这次会不会弹权限」，会弹的才推给你，不打扰。
- **前台不打扰**：Claude 终端在前台时不发通知，后台才发。
- **飞书 + 桌面并行，先点先生效**：手机点飞书卡片、本机点 toast/对话框，哪个先点哪个算。

## 工作原理

```
Claude Code
  ├─[PreToolUse] ccding.hooks.pretooluse  ← 交互授权（阻塞至多 ~290s）
  │   读 stdin → 权限闸门(不需授权则放行) → 焦点闸门(前台则交回终端)
  │   → 后台：并行 飞书卡片 + 桌面弹窗 → 收点击 → 输出 allow/deny（超时→ask 回落终端）
  │
  └─[Stop] ccding.hooks.stop             ← 完成通知（不阻塞）
      焦点闸门 → 取最后一条助手消息 → 飞书完成卡片 + 桌面纯通知
```

## 快速开始

```bash
# 1. 安装依赖（uv 会自动拉取 Python 3.12 与依赖）
uv sync

# 2. 配置：复制模板并填入飞书应用凭据
cp .env.example .env
#    然后跑一次拿到你的 open_id，填回 .env
uv run python -m ccding.scripts.setup_feishu

# 3. （Windows）注册自定义 AUMID，让交互 toast 可靠回调
uv run python -m ccding.scripts.register_win_aumid

# 4. 在 ~/.claude/settings.json 接上钩子（见 SETUP.md）
```

详细配置（飞书后台建应用、开权限、订阅事件、接钩子、端到端验证）见 **[SETUP.md](./doc/SETUP.md)**。

## 已知局限

- **焦点检测是启发式**（按进程祖先链 / 最前台 app 名比对），多终端复用、tmux/SSH、WSL 等可能误判；失败时一律保守按「后台」发通知。
- **读不到 `settings.json` 的 `permissions.allow` 白名单**，已永久允许的工具可能仍被推送 → 用 `.env` 的 `CCDING_ALWAYS_ALLOW` 手动补。
- **建连延迟**：每次后台授权有 ~1–2s 建飞书长连接的延迟；Claude 工具调用串行，不会并发抢长连接。
- **macOS 桌面授权用 `display dialog`**（模态对话框，会抢焦点）而非原生通知按钮——后者对非打包脚本不可靠。飞书按钮始终是跨平台主路径。
