"""日志配置。

关键约束：PreToolUse 钩子的 stdout 必须是纯净 JSON（决策结果），任何日志都不能写 stdout，
否则会污染钩子契约。故本模块的 handler 固定指向 stderr；若设置 CCDING_LOG_FILE，则同时追加写文件。
"""

import logging
import os
import sys

# 已配置过的 logger 名集合，避免重复 addHandler 造成日志重复
_configured: set[str] = set()
# stderr 是否已强制为 UTF-8（中文 Windows 默认 GBK 写管道，会被按 UTF-8 读取方读成乱码）
_stderr_fixed = False


def _force_utf8_stderr() -> None:
    """尽力把 stderr 重配为 UTF-8，避免中文日志在管道/捕获端乱码（幂等、失败静默）。"""
    global _stderr_fixed
    if _stderr_fixed:
        return
    _stderr_fixed = True
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass  # 老解释器 / 非标准流，忽略


def get_logger(name: str = "ccding") -> logging.Logger:
    """获取配置好的 logger。

    功能说明：
        返回一个只写 stderr（绝不写 stdout）的 logger，避免污染 PreToolUse 钩子要求的纯净
        JSON stdout；若设置环境变量 CCDING_LOG_FILE，则同时把日志追加写入该文件。幂等：
        同名 logger 重复调用不会重复添加 handler。
    参数：
        name: logger 名称，默认 "ccding"；通常各模块传入自身 __name__。
    返回值：
        已配置 handler 与级别的 logging.Logger 实例。
    """
    logger = logging.getLogger(name)
    if name in _configured:
        return logger

    _force_utf8_stderr()
    level_name = os.environ.get("CCDING_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # stderr handler：保证 stdout 干净
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    # 可选文件 handler
    log_file = os.environ.get("CCDING_LOG_FILE")
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        except OSError as exc:  # 文件不可写时降级为仅 stderr，不影响主流程
            logger.warning("无法打开日志文件 %s：%s（降级为仅 stderr）", log_file, exc)

    # 不向 root 传播，避免被外部 basicConfig 接到 stdout
    logger.propagate = False
    _configured.add(name)
    return logger
