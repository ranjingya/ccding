"""启动自动识别操作系统。

供 desktop（通知器）与 focus（前台检测）按平台选择具体实现，业务代码不直接写 if platform。
"""

import functools
import platform

from .log import get_logger

logger = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def get_platform() -> str:
    """识别当前操作系统（进程内只算一次）。

    功能说明：
        把 platform.system() 的结果归一化为内部短名，作为各平台工厂的分发依据。
        用 lru_cache 缓存：一次钩子进程内无论被调几次（desktop / focus 各会调），
        都只真正计算并记一次日志。无常驻进程，故无需也无法跨进程持久化（platform.system 本就近乎零成本）。
    参数：
        无。
    返回值：
        'win'（Windows）/ 'mac'（macOS）/ 'other'（其它，仅日志、不弹本机窗口）。
    """
    system = platform.system()
    name = {"Windows": "win", "Darwin": "mac"}.get(system, "other")
    logger.info("识别到操作系统：%s -> %s", system, name)
    return name
