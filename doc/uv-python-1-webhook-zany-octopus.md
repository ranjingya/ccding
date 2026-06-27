# ccding：用 uv + Python 重写 Claude Code 任务/授权通知系统

> 名字 **ccding** = claude code 的“叮”一声，承接旧仓库 ccdd（claude code 滴滴）的拟声含义，副标题：Claude Code 远程授权与完成提醒。

## Context（为什么做这个）

现有 `ccdd-master` 是一套 Node.js 脚本，只能在 Claude Code **停下来时**通过飞书自定义机器人 **Webhook** 发个单向通知 + Windows 声音。它有三个硬伤：

1. 只能用 Webhook，发不出**可交互**的消息（不能点按钮）。
2. Claude 停下来**请求权限**时没法把决策权交给手机——你还得回电脑点终端。
3. 平台耦合 Windows（PowerShell 声音），无 Mac 支持。

本次用 **Python + uv** 重写，目标：接入飞书**自定义应用**（不止 Webhook），在 Claude 请求权限时把请求推到飞书/桌面并**支持点按钮直接同意/拒绝**，决策回灌给 Claude Code；跨平台（Windows + macOS）且启动自动识别系统；**仅通知，无声音**。

**通道范围：只做飞书。** 旧版还带 Telegram 和 Windows 声音两条通道，新版一律不做——通知主路径是飞书自定义应用，桌面弹窗作为本机并行兜底。

旧仓库已克隆到本仓库同级目录 `d:\code\ccdd`（Node.js 实现，纯只读参考）。其中两段逻辑对新版有复用价值，见下文「可复用的旧版逻辑」。

经调研确认整套流程**可行**，关键机制见下。最终用户拍板的取舍：

- **架构：无常驻进程（daemon-less）。** 授权钩子在等待期间（最多 600s）自己临时建飞书长连接 + 弹窗，点了就返回并退出。无后台服务、无开机自启。
- **授权范围：跟随 Claude 的真实权限请求。** 用 `permission_mode` 还原“Claude 这次会不会弹权限”，会弹的才推给你。
- **前台不打扰：** Claude 窗口在前台时不发任何通知；后台才发。
- **完成也通知：** 不止权限请求，任务完成（Stop）也发。

---

## 调研确认的关键事实（设计地基）

**Claude Code 钩子**（官方文档 code.claude.com/docs/en/hooks）：

- `PreToolUse` 钩子能**程序化决定**是否放行工具，stdout 输出（stdin 用 snake_case，输出用 camelCase；JSON 仅在退出码 0 时生效，stdout 必须纯净）：
  ```json
  {"hookSpecificOutput":{"hookEventName":"PreToolUse",
    "permissionDecision":"allow|deny|ask|defer","permissionDecisionReason":"..."}}
  ```
  `permissionDecision` 四个取值：`allow`=直接放行（绕过终端弹窗）、`deny`=拦截并把 reason 回给 Claude、`ask`=强制走终端弹窗、`defer`/无输出=走 Claude 正常权限流程。本项目「不需授权」用**无输出**表达。
- `command` 类钩子默认超时 **600s**（秒），可用 `timeout` 字段调整——这是“阻塞等远程点击”可行的根因。
- `PreToolUse` 的 stdin 含：`session_id`、`cwd`、`transcript_path`、`permission_mode`、`tool_name`、`tool_input`。`permission_mode` 共**六个**取值：`default`/`plan`/`acceptEdits`/`auto`/`dontAsk`/`bypassPermissions`。
- `Stop` 钩子在 Claude 每轮回答结束时触发，其 stdin **直接提供 `last_assistant_message` 与 `stop_reason`**（无需解析 transcript，缺失时才回退解析）；无 `stop_hook_active` 字段。
- matcher：`"Bash|Edit|Write"` 多工具用 `|`，`"*"` 或空串=全部；含 `.`/`*` 等字符时整体按 JS 正则解析。

**飞书自定义应用 + lark-oapi**（PyPI `lark-oapi`）：

- 发交互卡片：`client.im.v1.message.create`，`msg_type="interactive"`，按钮带 `"behaviors":[{"type":"callback","value":{...}}]` 才会回调。
- 收按钮点击：**长连接 WebSocket**，无需公网 URL。`lark.ws.Client(app_id, app_secret, event_handler).start()`，事件类型 `card.action.trigger`，注册 `EventDispatcherHandler.builder("","").register_p2_card_action_trigger(handler)`（**无 `_v1` 后缀**）。
- handler 内 `data.event.action.value` 拿到按钮回传的 value、`data.event.operator.open_id` 拿点击人；**须 3s 内返回** toast。
- 后台需：启用机器人能力、开 `im:message:send_as_bot` 权限、事件订阅选「长连接」、订阅 `card.action.trigger`、发布并对你可见。

**桌面弹窗**：

- Windows：`windows-toasts`（`InteractableWindowsToaster`），按钮回调**仅在进程存活时**触发（无 COM 冷激活）——正好我们的阻塞钩子在等待期间是存活的。`ToastAudio(silent=True)` 静音。需用 `register_hkey_aumid` 注册一个自定义 AUMID。
- macOS：原生通知的动作按钮对非打包脚本不可靠；改用 `osascript -e 'display dialog ... buttons {"拒绝","同意"} default button "同意"'`，**阻塞并返回点了哪个键**，无需 app bundle，短进程也能用。纯通知用 `display notification`。

---

## 目标与需求映射

| 需求 | 方案 |
|---|---|
| ① 接入飞书**应用**（非 Webhook） | lark-oapi + app_id/app_secret，发交互卡片 + 长连接收回调 |
| ② 请求权限时发消息 + 飞书点按钮授权 | `PreToolUse` 钩子阻塞 → 发卡片 + 弹窗 → 收点击 → 回 `allow`/`deny` |
| ③ Windows 通知 + 授权按钮 | `windows-toasts` 交互 toast，与飞书并行，先点先生效 |
| ④ 仅通知，无声音 | toast `silent=True`；不调任何 TTS/Beep |
| ⑤ Mac 支持 + 启动自动识别系统 | `platform.system()` 分发；Mac 用 osascript dialog/notification |
| ⑥（追加）autoedit 下 Claude 要啥权限就通知啥 | 用 `permission_mode` + 工具族还原 Claude 是否会弹 |
| ⑦（追加）前台不通知、后台才通知 | 统一焦点闸门：判断 Claude 窗口是否前台 |
| ⑧（追加）完成也通知 | `Stop` 钩子发完成卡片（同样受焦点闸门约束） |

---

## 架构总览（无常驻进程）

每个钩子是**短命进程**，自己完成全部工作后退出：

```
Claude Code
  ├─[PreToolUse 钩子] hooks/pretooluse.py   ← 交互授权（会阻塞最多 ~290s）
  │     1. 读 stdin(JSON) → 解析 tool_name / permission_mode / tool_input
  │     2. 权限闸门：这次 Claude 会弹权限吗？不会 → 输出 {} 立即放行退出
  │     3. 焦点闸门：Claude 在前台？是 → 输出 ask（交给终端），不打扰，退出
  │     4. 后台且需授权 → 并行：
  │            · 飞书：建长连接(WS) → 发授权卡片(同意/拒绝按钮)
  │            · 桌面：Win toast 按钮 / Mac osascript dialog
  │        等「任一渠道先点击」或超时
  │     5. 输出 permissionDecision=allow/deny（超时→ask 回落终端），退出
  │
  └─[Stop 钩子] hooks/stop.py               ← 完成通知（不阻塞）
        1. 焦点闸门：前台 → 直接退出
        2. 从 stdin/transcript 取最后一条助手消息当正文
        3. 飞书发完成卡片 + 桌面纯通知（无按钮），退出
```

“无常驻进程”的代价：每次后台授权要花 ~1–2s 建飞书长连接；建连期间的点击才有效。Claude 工具调用是**串行**的（一次授权会阻塞 Claude），所以“同一应用单长连接实例”的限制不会撞车。

---

## 项目结构

新建独立 uv 项目，就放在当前仓库 `d:\code\ccding`（与只读参考的旧仓库 `d:\code\ccdd` 同级，互不干扰）：

钩子与脚本放在**包内部**（而非顶层），这样 `python -m ccding.hooks.xxx` 从任意 Claude 会话目录都能解析到——钩子运行时的真实 cwd 从 stdin 的 `cwd` 字段读，不依赖进程当前目录。

```
ccding/                    # = d:\code\ccding，本仓库根
  pyproject.toml          # uv 项目；依赖见下
  .python-version         # 钉 3.12（避开刚发布的 3.14 二进制轮子）
  .env.example            # 配置模板
  README.md / SETUP.md    # 中文说明（沿用旧仓库风格）
  settings.hooks.example.json  # 可直接合并进 ~/.claude/settings.json 的 hooks 块
  ccding/                 # 包目录
    __init__.py
    log.py                # 日志：固定写 stderr（不污染钩子 stdout），可选写文件，强制 UTF-8
    constants.py          # 共享决策常量 APPROVE / DENY
    config.py             # 读 .env：app_id/secret、接收人、超时、工具分类；项目名识别
    platform_detect.py    # 启动自动识别系统 → 返回 notifier & 焦点检测实现
    focus.py              # 前台/后台检测（Win: ctypes+psutil；Mac: osascript）
    gate.py               # 权限闸门：根据 permission_mode(6 种) + tool_name 判断是否需授权
    feishu.py             # lark-oapi：发卡片 + 长连接收 card.action.trigger
    desktop.py            # 抽象 Notifier；WindowsNotifier / MacNotifier / NullNotifier
    approval.py           # 编排：并行飞书+桌面，先点先赢，超时回落
    cards.py              # 飞书卡片 JSON 构造（授权卡 / 完成卡，schema 2.0）
    hooks/
      __init__.py
      pretooluse.py       # PreToolUse 入口（python -m ccding.hooks.pretooluse）
      stop.py             # Stop 入口（python -m ccding.hooks.stop）
    scripts/
      __init__.py
      setup_feishu.py       # 一次性：拿你的 open_id（监听你给机器人发的第一条消息）
      register_win_aumid.py # 直接写注册表注册自定义 AUMID（Windows）
```

依赖（`pyproject.toml`，已按实际解析锁定；lark-oapi 新版为纯 Python、不再依赖 protobuf）：

```toml
[project]
requires-python = ">=3.10,<3.14"      # 下限随 python-dotenv 1.2.x；上限避开 3.14-only 轮子
dependencies = [
  "lark-oapi>=1.6,<2",                  # 纯 Python 轮子，无 protobuf（解析为 1.6.9）
  "python-dotenv>=1.0",                 # 解析为 1.2.x
  "psutil>=5.9",                        # 焦点检测；解析为 7.x（cp37-abi3 轮子覆盖 3.12）
  # 仅 Windows：必须自带 sys_platform 标记，否则非 Windows 会去解析 windows-only 的 winrt-* 而失败
  "windows-toasts>=1.3 ; sys_platform == 'win32'",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

> Mac 不需要额外依赖（osascript 走 subprocess，stdlib 即可）。`.python-version` 钉 3.12，`uv sync` 自动拉取受管 CPython 3.12。

---

## 关键组件设计

### 1. 启动自动识别系统 `platform_detect.py`
```python
import platform
def get_platform():
    s = platform.system()           # 'Windows' / 'Darwin' / 'Linux'
    return {'Windows':'win','Darwin':'mac'}.get(s, 'other')
```
据此选择 `desktop.py` 里的 `WindowsNotifier` / `MacNotifier` / `NullNotifier`，以及 `focus.py` 的对应实现。所有平台分支集中在这两个工厂里，业务代码不写 `if platform`。

### 2. 焦点闸门 `focus.py`（前台不打扰）
判断「运行本 Claude Code 会话的终端窗口」是否当前前台：
- **Windows**：`ctypes.windll.user32.GetForegroundWindow()` → `GetWindowThreadProcessId` 拿前台 PID；用 `psutil` 从当前钩子进程沿父链上溯，若前台 PID 在祖先链中 → 前台。
- **macOS**：`osascript -e 'tell application "System Events" to get name of first process whose frontmost is true'` 拿最前台 app 名；与钩子的祖先进程名（Terminal/iTerm/ghostty 等）比对。
- **其它/失败**：保守返回「后台」（宁可多发一条，也别漏）。

启发式、非 100% 精确，已在 caveats 说明。

### 3. 权限闸门 `gate.py`（跟随 Claude 真实权限请求）
输入 `permission_mode`（六种）+ `tool_name`，返回 `need_approval: bool`：
- `bypassPermissions` / `dontAsk` / `auto` → False（Claude 不会问，插入授权只会无谓阻塞）。
- `plan` → False（计划模式工具本就被限制）。
- `acceptEdits` → 编辑族（`Edit/Write/MultiEdit/NotebookEdit`）= False（Claude 自动接受，不打扰）；其余（`Bash/WebFetch/WebSearch/MCP…`）= True。
- `default` 及未知模式 → 只读工具（`Read/Grep/Glob/...`）= False，其余 = True。

编辑族 / 只读族 / 永久白名单写成可被环境变量覆盖的默认常量（`config.py`），并在 `settings.json` 的 matcher 里同样收敛，双重保险。**局限**：无法读取你 `settings.json` 里的 `permissions.allow` 白名单，故个别你已永久允许的工具仍可能被推送——可用 `.env` 的 `CCDING_ALWAYS_ALLOW` 手动补齐。

### 4. 飞书 `feishu.py`
- `send_card(card_json) -> bool`：`client.im.v1.message.create`，接收人来自 config（`open_id` 或群 `chat_id`）。
- `wait_card_click(value_match, timeout) -> 'approve'|'deny'|None`：临时起 `lark.ws.Client`，注册 `register_p2_card_action_trigger`，handler 里读 `action.value`，匹配本次请求的 `req_id`，把结果塞进线程安全队列并 `return P2CardActionTriggerResponse({"toast":{"type":"success","content":"已处理"}})`；主线程阻塞等队列/超时；拿到后停 WS。
- 卡片用 `cards.py` 构造：标题（项目名 + 工具名）、正文（命令/参数摘要，截断）、两个 callback 按钮（同意/拒绝，value 带唯一 `req_id`）。

### 5. 桌面 `desktop.py`
- `WindowsNotifier`：`InteractableWindowsToaster(notifierAUMID=...)`；授权用带「批准/拒绝」按钮的 toast + `on_activated` 回调（进程存活期间有效），`ToastAudio(silent=True)`；纯通知用无按钮 toast。
- `MacNotifier`：授权用 `osascript display dialog ... buttons {"拒绝","同意"}`（阻塞返回点了哪个）；纯通知用 `display notification`。
- `NullNotifier`：其它平台，仅日志。

### 6. 编排 `approval.py`
`request_approval(tool, detail, timeout) -> 'allow'|'deny'|'timeout'`：用线程并行跑「飞书 `wait_card_click`」和「桌面按钮等待」，`first-wins`（任一返回即取其结果、取消另一路）；都超时 → `timeout`。内部超时设 ~290s（< 钩子 600s），给退出留余量。

### 7. 钩子入口
- `hooks/pretooluse.py`：读 stdin → `gate` 判断；不需授权 → `print("")`/不输出（放行）退出；需授权但前台 → 输出 `permissionDecision: ask`（交回终端）退出；后台 → `approval.request_approval`，`allow`/`deny` 按结果输出，`timeout` → 输出 `ask` 回落终端。**所有 stdout 必须是纯净 JSON**（日志走 stderr/文件，别污染 stdout）。
- `hooks/stop.py`：焦点闸门；从 stdin JSON 的 `last_assistant_message`（或读 `transcript_path` 尾部）取正文，发完成卡片 + 桌面纯通知。

---

## 可复用的旧版逻辑（移植自 `d:\code\ccdd`）

旧版飞书发送走 Webhook、与新版应用 API 完全不同，需重写；但「通知内容怎么生成」这部分与通道无关，直接移植到 Python：

### A. 项目名识别 → `config.py`
按优先级取项目名，作为卡片标题前缀：

1. 当前工作目录 `pyproject.toml` 的 `[project].name`（旧版读 `package.json`，Python 项目换成此项）；
2. `git remote get-url origin`，从 URL 正则 `/([^/]+)\.git$/` 提仓库名；
3. 兜底用 `cwd` 的目录名。

每一步加日志说明命中了哪条来源；全失败用「未知项目」。

### B. 完成通知正文生成 → `stop.py`
从 Stop 钩子 stdin 的 JSON 直接取 `last_assistant_message`（缺失才回退解析 `transcript_path`），清洗后作正文：

- 按行拆分，过滤空行与 `#` 开头的标题行；
- 取前 5 行，`join(' ')` 后截断到约 1500 字符；
- 取不到内容则用「任务完成」。

标题统一 `{项目名} · 已完成`（授权卡标题则为 `{项目名} · {工具名}`）。

---

## settings.json 钩子接线（`~/.claude/settings.json`）

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch|WebSearch|mcp__.*",
        "hooks": [
          { "type": "command",
            "command": "uv run --project D:/code/ccding python -m ccding.hooks.pretooluse",
            "timeout": 600 } ] }
    ],
    "Stop": [
      { "hooks": [
          { "type": "command",
            "command": "uv run --project D:/code/ccding python -m ccding.hooks.stop" } ] }
    ]
  }
}
```
（matcher 与 `gate.py` 的敏感集对齐；用 `uv run --project` 保证用对虚拟环境与依赖。）

---

## 需要你做的事（实施前/中）

1. **飞书后台**（最关键，只能你来）：创建一个**自定义应用** → 启用「机器人」能力 → 权限管理开 `im:message:send_as_bot` → 事件与回调选「**使用长连接接收**」并订阅 `card.action.trigger` → 发布、对你自己可见。把 **app_id / app_secret** 给我。
2. **接收人**：拿到 app 后，跑 `uv run python -m ccding.scripts.setup_feishu`（需临时加订阅 `im.message.receive_v1`），你给机器人发一条消息，脚本打印你的 `open_id`（或你建个含机器人的群，用 `chat_id`），填进 `.env`。
3. **Windows**：跑一次 `uv run python -m ccding.scripts.register_win_aumid` 注册自定义 AUMID（否则 toast 移入通知中心后点击不回调）。
4. 已完成：uv 项目、全部模块、`.env.example`、`settings.hooks.example.json` 钩子、冒烟测试。

`.env` 主要字段：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_RECEIVE_ID`、`FEISHU_RECEIVE_ID_TYPE`(open_id/chat_id)、`APPROVAL_TIMEOUT`、`WIN_AUMID`。

---

## 验证（端到端）

1. **单元**：`uv run python -c "from ccding.platform_detect import get_platform; print(get_platform())"` 确认识别正确。
2. **飞书发送**：跑一个小脚本发完成卡片，确认手机/手环收到。
3. **飞书回调**：发授权卡片，手机点「同意」，确认 `wait_card_click` 返回 `approve`。
4. **桌面**：Windows 跑授权 toast，点按钮确认 `on_activated` 拿到决策；Mac 跑 `display dialog` 确认返回值。
5. **焦点闸门**：分别在「Claude 终端在前台」与「切到别的窗口」两种情况触发，确认前台不发、后台发。
6. **真实联调**：接好钩子，在 acceptEdits 模式下让 Claude 跑一个 `Bash` 命令（切走窗口）→ 手机应收到授权卡片，点同意 → Claude 继续；再让它编辑文件 → **不应**被打扰（acceptEdits 自动接受）。任务结束 → 收到完成通知。

---

## 已知局限（务必告知用户）

- 焦点检测是启发式（按进程祖先链 / 最前台 app 名），多终端复用、tmux/SSH、WSL 等场景可能误判；失败时保守按“后台”发通知。
- 权限闸门读不到你 `settings.json` 的 `permissions.allow` 白名单，已永久允许的工具可能仍被推送 → 用 `.env` 的 `CCDING_ALWAYS_ALLOW` 手动补。
- 无常驻进程：每次后台授权有 ~1–2s 建连延迟；Claude 串行调用，不会并发抢长连接。
- Mac 桌面授权用 `display dialog`（模态对话框，会抢焦点）而非原生通知按钮——因后者对非打包脚本不可靠（飞书按钮始终是跨平台主路径）。Mac 取最前台 app 需对父终端授予「自动化」权限，headless/SSH 下取不到则保守判后台。
- lark-oapi 新版为纯 Python，底层依赖 `requests/httpx/websockets/pycryptodome`，已不再依赖 protobuf；`.python-version` 钉 3.12 以避开 3.14-only 轮子。
