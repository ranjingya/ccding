"""配置加载与项目元信息识别。

读取项目根 .env 中的飞书应用凭据/接收人/超时等，提供：
  - load_config(): 返回结构化 Config；
  - detect_project_name(): 用作通知标题前缀的项目名识别（pyproject.toml > git 远程 > 目录名）。

工具分类（编辑族/只读族/永久白名单）写成可被环境变量覆盖的默认常量，与 settings.json 的 matcher 双重收敛。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .log import get_logger

logger = get_logger(__name__)

# 项目根目录（包目录 ccding/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 工具分类默认值，可经对应环境变量（逗号分隔）覆盖
DEFAULT_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DEFAULT_READONLY_TOOLS = {"Read", "Grep", "Glob", "NotebookRead", "LS", "TodoWrite"}

# 内部等待上限默认值（秒），略小于钩子 600s 总超时，给退出留余量
DEFAULT_APPROVAL_TIMEOUT = 290
# 默认 AUMID（Windows toast 注册用），用户可经 WIN_AUMID 覆盖
DEFAULT_WIN_AUMID = "ccding.claudecode.approval"

_env_loaded = False


@dataclass
class Config:
    """运行期配置。

    字段：
        app_id / app_secret: 飞书自定义应用凭据。
        receive_id: 接收人标识（open_id 或群 chat_id）。
        receive_id_type: 接收人类型，"open_id" 或 "chat_id"。
        approval_timeout: 钩子内部等待远程点击的上限秒数。
        win_aumid: Windows toast 使用的自定义 AUMID。
        edit_tools / readonly_tools / always_allow: 供权限闸门使用的工具集合。
    """

    app_id: str
    app_secret: str
    receive_id: str
    receive_id_type: str
    approval_timeout: int
    win_aumid: str
    feishu_enabled: bool
    desktop_enabled: bool
    edit_tools: set[str]
    readonly_tools: set[str]
    always_allow: set[str]

    @property
    def feishu_ready(self) -> bool:
        """飞书三要素（app_id/app_secret/receive_id）是否齐备。"""
        return bool(self.app_id and self.app_secret and self.receive_id)


def _parse_env_file(path: Path) -> dict[str, str]:
    """手动解析 .env（python-dotenv 缺失时的兜底）。

    功能说明：逐行解析 KEY=VALUE，忽略空行与 # 注释，去除值两端引号。
    参数：
        path: .env 文件路径。
    返回值：键值字典。
    """
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _ensure_env_loaded() -> None:
    """确保项目根 .env 已加载（幂等）。

    功能说明：优先用 python-dotenv 加载项目根 .env；未安装则手动解析；都不存在时仅用系统环境变量。
    参数：无。
    返回值：无（结果写入 os.environ）。
    """
    global _env_loaded
    if _env_loaded:
        return

    env_path = _PROJECT_ROOT / ".env"
    try:
        from dotenv import load_dotenv

        if env_path.is_file():
            load_dotenv(env_path)
            logger.info(".env 已加载：%s", env_path)
        else:
            load_dotenv()  # 退回 dotenv 默认查找路径
            logger.warning("项目根无 .env，改用 dotenv 默认查找")
    except ImportError:
        if env_path.is_file():
            for key, value in _parse_env_file(env_path).items():
                os.environ.setdefault(key, value)
            logger.info(".env 已手动解析加载：%s", env_path)
        else:
            logger.warning("未找到 .env 且未安装 python-dotenv，仅使用系统环境变量")

    _env_loaded = True


def _split_env(name: str, default: set[str]) -> set[str]:
    """读取逗号分隔的环境变量为集合，留空则用默认值。"""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return set(default)
    return {item.strip() for item in raw.split(",") if item.strip()}


def _bool_env(name: str, default: bool = True) -> bool:
    """读取布尔环境变量，缺省/留空用 default；true/1/yes/on（不分大小写）为真。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_config() -> Config:
    """加载运行期配置。

    功能说明：
        确保 .env 已加载后，从环境变量读取飞书凭据/接收人/超时/AUMID 及工具分类集合，
        组装为 Config。关键字段缺失不抛异常，由调用方据 feishu_ready 决定降级行为。
    参数：
        无。
    返回值：
        组装好的 Config 实例。
    """
    _ensure_env_loaded()

    def _get(name: str) -> str:
        return os.environ.get(name, "").strip()

    try:
        timeout = int(os.environ.get("APPROVAL_TIMEOUT", "").strip() or DEFAULT_APPROVAL_TIMEOUT)
    except ValueError:
        logger.warning("APPROVAL_TIMEOUT 非法，回退默认 %ss", DEFAULT_APPROVAL_TIMEOUT)
        timeout = DEFAULT_APPROVAL_TIMEOUT

    config = Config(
        app_id=_get("FEISHU_APP_ID"),
        app_secret=_get("FEISHU_APP_SECRET"),
        receive_id=_get("FEISHU_RECEIVE_ID"),
        receive_id_type=_get("FEISHU_RECEIVE_ID_TYPE") or "open_id",
        approval_timeout=timeout,
        win_aumid=_get("WIN_AUMID") or DEFAULT_WIN_AUMID,
        feishu_enabled=_bool_env("CCDING_FEISHU_ENABLED", True),
        desktop_enabled=_bool_env("CCDING_DESKTOP_ENABLED", True),
        edit_tools=_split_env("CCDING_EDIT_TOOLS", DEFAULT_EDIT_TOOLS),
        readonly_tools=_split_env("CCDING_READONLY_TOOLS", DEFAULT_READONLY_TOOLS),
        always_allow=_split_env("CCDING_ALWAYS_ALLOW", set()),
    )
    logger.info(
        "配置加载完成：feishu_ready=%s feishu_enabled=%s desktop_enabled=%s receive_id_type=%s timeout=%ss aumid=%s",
        config.feishu_ready,
        config.feishu_enabled,
        config.desktop_enabled,
        config.receive_id_type,
        config.approval_timeout,
        config.win_aumid,
    )
    return config


def detect_project_name(cwd: str | None = None) -> str:
    """识别项目名，用作通知标题前缀。

    功能说明：
        按优先级返回项目名 —— pyproject.toml 的 [project].name > git 远程仓库名 > 当前目录名 >
        "未知项目"。每命中一条来源都记日志，便于排查标题不符预期的情况。
    参数：
        cwd: 起始目录；默认取当前工作目录（即 Claude 会话的 cwd）。
    返回值：
        项目名字符串。
    """
    base = Path(cwd or os.getcwd())

    # 1) pyproject.toml 的 [project].name
    pyproject = base / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            name = (data.get("project") or {}).get("name")
            if name:
                logger.info("从 pyproject.toml 识别项目名：%s", name)
                return name
        except Exception as exc:  # 解析失败不致命，继续下一来源
            logger.warning("解析 pyproject.toml 失败：%s", exc)

    # 2) git 远程仓库名
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # rstrip("/") 兼容结尾带斜杠的远程地址（…/repo/）
            match = re.search(r"/([^/]+?)(?:\.git)?$", result.stdout.strip().rstrip("/"))
            if match:
                logger.info("从 git 远程识别项目名：%s", match.group(1))
                return match.group(1)
    except Exception as exc:  # 非 git 目录 / 无 git 命令，继续兜底
        logger.warning("读取 git 远程失败：%s", exc)

    # 3) 目录名兜底
    name = base.name or "未知项目"
    logger.info("从目录名识别项目名：%s", name)
    return name
