# -*- coding: utf-8 -*-
"""ccding 回归测试（纯逻辑 + 钩子契约，不触发真实 UI/飞书）。

运行：uv run python tests/test_ccding.py
覆盖：模块导入、卡片构造、权限闸门（含 acceptEdits 只读放行）、项目名正则、
      stdin UTF-8 读取、stop 正文清洗、授权编排无渠道快速返回、
      钩子决策映射与 stdout 纯净性（打桩规避 UI）、子进程 cwd 无关性与 UTF-8 中文 stdin。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


# 1) 导入
import ccding.log, ccding.platform_detect, ccding.config, ccding.gate
import ccding.constants, ccding.cards, ccding.desktop, ccding.focus
import ccding.approval, ccding.feishu
import ccding.hooks.pretooluse as pre
import ccding.hooks.stop as stopmod
import ccding.scripts.register_win_aumid, ccding.scripts.setup_feishu
check("import all modules", True)

# 2) 卡片
def find_buttons(node):
    """递归找出卡片里所有 button（按钮现嵌在 column_set/column 内）。"""
    out = []
    if isinstance(node, dict):
        if node.get("tag") == "button":
            out.append(node)
        for v in node.values():
            out.extend(find_buttons(v))
    elif isinstance(node, list):
        for x in node:
            out.extend(find_buttons(x))
    return out

ac = ccding.cards.build_approval_card("proj · Bash", "rm -rf /tmp/x", "req123")
json.dumps(ac)
btns = find_buttons(ac)
vals = [b["behaviors"][0]["value"] for b in btns]
check("approval card: 2 callback buttons w/ req_id+decision",
      len(btns) == 2 and all(v["req_id"] == "req123" for v in vals)
      and {v["decision"] for v in vals} == {"approve", "deny"})
check("approval card: 命令在 markdown 代码块",
      any(e.get("tag") == "markdown" and e.get("content", "").startswith("```")
          for e in ac["body"]["elements"]))

def find_tag(node, tag):
    out = []
    if isinstance(node, dict):
        if node.get("tag") == tag:
            out.append(node)
        for v in node.values():
            out.extend(find_tag(v, tag))
    elif isinstance(node, list):
        for x in node:
            out.extend(find_tag(x, tag))
    return out

check("approval card: form + instruction 输入框",
      len(find_tag(ac, "form")) == 1
      and any(i.get("name") == "instruction" for i in find_tag(ac, "input")))
check("approval card: 提交按钮 form_action_type=submit",
      len(btns) == 2 and all(b.get("form_action_type") == "submit" for b in btns))

rc = ccding.cards.build_resolved_card("proj · Bash", "rm -rf /tmp/x", "approve", "记得备份")
json.dumps(rc)
check("resolved card: 无按钮 + 含指示",
      not find_buttons(rc) and "记得备份" in rc["body"]["elements"][-1]["content"]
      and rc["header"]["template"] == "green")
cc = ccding.cards.build_completion_card("proj · 已完成", "done")
json.dumps(cc)
check("completion card: no buttons", not find_buttons(cc))

# 3) 权限闸门
from ccding.gate import need_approval
from ccding.config import load_config
cfg = load_config()
gate_cases = {
    ("default", "Bash"): True, ("default", "Read"): False, ("default", "Edit"): True,
    ("acceptEdits", "Edit"): False, ("acceptEdits", "Read"): False, ("acceptEdits", "Bash"): True,
    ("plan", "Bash"): False, ("bypassPermissions", "Bash"): False,
    ("dontAsk", "Bash"): False, ("auto", "Bash"): False,
}
for (mode, tool), exp in gate_cases.items():
    check(f"gate {mode}/{tool} == {exp}", need_approval(mode, tool, cfg) is exp)

# 4) 项目名正则
import re
def proj(u):
    m = re.search(r"/([^/]+?)(?:\.git)?$", u.strip().rstrip("/"))
    return m.group(1) if m else None
check("git url trailing slash -> repo", proj("https://github.com/o/repo/") == "repo")
check("git ssh -> repo", proj("git@github.com:o/repo.git") == "repo")

# 5) stdin UTF-8 读取助手（StringIO 走文本分支）
old_in = sys.stdin
try:
    sys.stdin = io.StringIO('{"x":"中文"}')
    check("pretooluse._read_stdin_utf8 (StringIO)", json.loads(pre._read_stdin_utf8())["x"] == "中文")
    sys.stdin = io.StringIO('{"y":"完成"}')
    check("stop._read_stdin_utf8 (StringIO)", json.loads(stopmod._read_stdin_utf8())["y"] == "完成")
finally:
    sys.stdin = old_in

# 6) stop 正文清洗
check("stop _clean_message filters # & blanks",
      stopmod._clean_message("# 标题\n\n第一行\n第二行\n") == "第一行 第二行")
check("stop _clean_message empty -> 任务完成", stopmod._clean_message("") == "任务完成")

# 7) 授权编排：无渠道快速 (None, "")
from ccding.desktop import NullNotifier
class _Unready:
    ready = False
    def request_approval(self, *a, **k): return None, ""
t0 = time.monotonic()
r = ccding.approval.request_approval(NullNotifier(), _Unready(), "t", "b", "rid", timeout=2)
check("approval no channels -> (None, '') fast", r == (None, "") and time.monotonic() - t0 < 1.5)

# 8) 钩子决策映射（打桩规避真实 UI）；request_approval 现返回 (decision, note)
def run_pre(stdin_obj, fg=None, decision="__none__", note=""):
    if fg is not None:
        ccding.focus.is_claude_foreground = (lambda: fg)
    if decision != "__none__":
        ccding.approval.request_approval = (lambda *a, **k: (decision, note))
    oi, oo, oe = sys.stdin, sys.stdout, sys.stderr
    out = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(stdin_obj)); sys.stdout = out; sys.stderr = io.StringIO()
        pre.main()
    finally:
        cap = out.getvalue(); sys.stdin, sys.stdout, sys.stderr = oi, oo, oe
    return cap

def hso(s):
    return json.loads(s)["hookSpecificOutput"]

base = {"tool_name": "Bash", "permission_mode": "default", "tool_input": {"command": "ls"}}
check("foreground -> ask", hso(run_pre(base, fg=True))["permissionDecision"] == "ask")
check("approve -> allow", hso(run_pre(base, fg=False, decision="approve"))["permissionDecision"] == "allow")
check("deny -> deny", hso(run_pre(base, fg=False, decision="deny"))["permissionDecision"] == "deny")
check("timeout -> ask", hso(run_pre(base, fg=False, decision=None))["permissionDecision"] == "ask")
check("bypass -> empty stdout",
      run_pre({"tool_name": "Bash", "permission_mode": "bypassPermissions", "tool_input": {}}) == "")
# 补充指示：拒绝时进 reason、同意时进 additionalContext
d = hso(run_pre(base, fg=False, decision="deny", note="改用 git rm --cached"))
check("deny+指示 -> reason 携带指示", d["permissionDecisionReason"] == "改用 git rm --cached")
a = hso(run_pre(base, fg=False, decision="approve", note="记得先备份"))
check("approve+指示 -> additionalContext", a.get("additionalContext") == "记得先备份")

# 9) 子进程：cwd 无关性 + UTF-8 中文 stdin（像 Claude 一样喂 UTF-8 字节）
def sub(stdin_obj, level="ERROR"):
    p = subprocess.run([sys.executable, "-m", "ccding.hooks.pretooluse"],
                       input=json.dumps(stdin_obj, ensure_ascii=False).encode("utf-8"),
                       capture_output=True, cwd=tempfile.gettempdir(),
                       env={**os.environ, "CCDING_LOG_LEVEL": level})
    return p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"), p.returncode

so, se, rc = sub({"tool_name": "Read", "permission_mode": "default", "tool_input": {}})
check("subprocess(Read) other cwd: empty stdout, exit 0", so == "" and rc == 0, f"rc={rc} out={so!r}")
zh = {"tool_name": "Read", "permission_mode": "default",
      "tool_input": {"file_path": "D:/代码/项目/文档.md"}, "cwd": "D:/代码/项目"}
so, se, rc = sub(zh, level="INFO")
check("subprocess UTF-8 中文 stdin: no parse-fail, empty stdout, exit 0",
      so == "" and rc == 0 and "解析 stdin 失败" not in se, f"rc={rc}")

print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
