"""一次性注册 Windows 自定义 AUMID。

为什么需要：交互式 toast 移入「通知中心」后再点击按钮，回调只在使用自定义 AUMID 时才可靠触发；
注册自定义 AUMID 还能让 toast 显示自定义名称/图标。

做了什么：直接写注册表 HKCU\\SOFTWARE\\Classes\\AppUserModelId\\{aumid}，设置 DisplayName
（及可选 IconUri）。不依赖 windows-toasts 内部函数（其注册函数未从包导出）。

用法：
    uv run python -m ccding.scripts.register_win_aumid
    uv run python -m ccding.scripts.register_win_aumid --aumid my.app.id --name "审批助手" --icon C:/path/app.ico

不带参数时，AUMID 取自 .env 的 WIN_AUMID（缺省 ccding.claudecode.approval）。
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_config
from ..log import get_logger
from ..platform_detect import get_platform

logger = get_logger(__name__)


def register_aumid(aumid: str, display_name: str, icon_path: str | None = None) -> bool:
    """把自定义 AUMID 写入当前用户注册表。

    功能说明：
        在 HKCU\\SOFTWARE\\Classes\\AppUserModelId\\{aumid} 下写入 DisplayName（REG_SZ），
        若提供图标则一并写入 IconUri。仅 Windows 有效。
    参数：
        aumid: 要注册的 AppUserModelID，需与 toast 使用的 notifierAUMID 一致。
        display_name: 通知中显示的应用名。
        icon_path: 可选图标文件路径（.ico/.png）。
    返回值：
        成功 True，失败 False。
    """
    if get_platform() != "win":
        logger.error("当前不是 Windows，无需且无法注册 AUMID")
        return False

    import winreg

    key_path = rf"SOFTWARE\Classes\AppUserModelId\{aumid}"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
            if icon_path:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
        logger.info(
            "已注册 AUMID：%s（DisplayName=%s%s）",
            aumid,
            display_name,
            f" IconUri={icon_path}" if icon_path else "",
        )
        return True
    except OSError as exc:
        logger.error("注册 AUMID 失败：%s", exc)
        return False


def main() -> int:
    """命令行入口：解析参数并注册 AUMID。"""
    config = load_config()
    parser = argparse.ArgumentParser(description="注册 Windows 自定义 AUMID（ccding toast 用）")
    parser.add_argument("--aumid", default=config.win_aumid, help="AppUserModelID，默认取 .env 的 WIN_AUMID")
    parser.add_argument("--name", default="ccding 审批助手", help="通知显示名")
    parser.add_argument("--icon", default=None, help="可选图标文件路径")
    args = parser.parse_args()

    ok = register_aumid(args.aumid, args.name, args.icon)
    if ok:
        print(f"✅ AUMID 已注册：{args.aumid}")
        print("   现在 .env 的 WIN_AUMID 应与此一致，交互 toast 即可可靠回调。")
        return 0
    print("❌ AUMID 注册失败，详见上面的日志。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
