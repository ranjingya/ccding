# 配置指南

按顺序走完即可。其中飞书后台部分只能你手动操作。

## 1. 安装

```bash
uv sync
```

uv 会按 `.python-version` 拉取 Python 3.14，并装好 `lark-oapi` 等依赖；`windows-toasts` 仅在 Windows 上安装。

## 2. 飞书后台：创建自定义应用

1. 打开[飞书开放平台](https://open.feishu.cn/) → 开发者后台 → **创建企业自建应用**。
2. **添加应用能力 → 机器人**（启用机器人）。
3. **权限管理**，开通：
   - `im:message:send_as_bot`（以应用身份发消息，**常驻必需**）；
   - `im:message.p2p_msg:readonly`（接收用户发给机器人的单聊消息，**仅用于第 4 步拿 open_id**，拿到后可取消）。
4. **事件与回调 → 订阅方式**：选择「**使用长连接接收事件**」，然后订阅：
   - `card.action.trigger`（卡片按钮回调，**常驻必需**）；
   - `im.message.receive_v1`（接收消息，**仅用于第 4 步拿 open_id**，拿到后可取消）。
5. **版本管理与发布**：创建版本并发布，确保应用对你自己可见。**权限或事件改动后都必须重新创建并发布新版本才生效**——只在后台改而不发版本，看着配好了实际不推送。
6. 到 **凭证与基础信息** 抄下 **App ID** 和 **App Secret**。

## 3. 填写 .env

```bash
cp .env.example .env
```

填入 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。其余字段说明见模板内注释。

## 4. 拿到接收人 open_id

```bash
uv run python -m ccding.scripts.setup_feishu
```

运行后在飞书里**给你的机器人发任意一条消息**，脚本会打印你的 `open_id`。把它填进 `.env`：

```
FEISHU_RECEIVE_ID=ou_你的openid
FEISHU_RECEIVE_ID_TYPE=open_id
```

> 也可以建一个含机器人的群，用群的 `chat_id`（`oc_...`）并设 `FEISHU_RECEIVE_ID_TYPE=chat_id`。


## 5.（仅 Windows）注册 AUMID

```bash
uv run python -m ccding.scripts.register_win_aumid
```

它把 `.env` 里 `WIN_AUMID` 的值写入注册表。不注册的话，交互 toast 移入「通知中心」后再点击按钮可能不回调。

## 6. 接上 Claude Code 钩子

编辑 `~/.claude/settings.json`，把下面的 `hooks` 块合并进去（已有 hooks 则并入，注意把路径换成你的实际路径）：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch|WebSearch|mcp__.*",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project D:/code/ccding python -m ccding.hooks.pretooluse",
            "timeout": 600
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project D:/code/ccding python -m ccding.hooks.stop"
          }
        ]
      }
    ]
  }
}
```

- `--project D:/code/ccding` 保证用对虚拟环境与依赖；`-m ccding.hooks.xxx` 因包已安装，从任意会话目录都能解析。
- `matcher` 决定哪些工具会进钩子；具体是否需要授权由 `ccding/gate.py` 据 `permission_mode` 再判一次。
- 仓库根的 `settings.hooks.example.json` 是同样内容，可直接拷贝。

## 7. 端到端验证

1. **平台识别**：`uv run python -c "from ccding.platform_detect import get_platform; print(get_platform())"`。
2. **发送**：配好 `.env` 后跑 `uv run python -c "from ccding.config import load_config; from ccding.feishu import get_channel; print(get_channel(load_config()).send_completion('测试','正文'))"`，打印 `True` 且手机/手环收到即正常。
3. **回调**：在 `acceptEdits` 模式让 Claude 跑一条 `Bash` 命令并**切走窗口** → 手机应收到授权卡片，点「同意」→ Claude 继续。
4. **不打扰**：让它**编辑文件**（acceptEdits 自动接受）→ 不应被打扰；窗口在前台时触发 → 不应收到通知。
5. **完成**：任务结束 → 收到完成通知。

## 故障排除

- **收不到卡片**：检查 App ID/Secret、`im:message:send_as_bot` 权限、应用是否发布且对你可见、`FEISHU_RECEIVE_ID(_TYPE)` 是否正确。
- **点了按钮没反应**：确认事件订阅选了「长连接」且订阅了 `card.action.trigger`。
- **toast 不回调（Windows）**：跑第 5 步注册 AUMID，并确保 `.env` 的 `WIN_AUMID` 与注册值一致。
- **想看日志**：`.env` 里设 `CCDING_LOG_FILE=D:/code/ccding/ccding.log`（日志只走 stderr/文件，不污染钩子 stdout）。
