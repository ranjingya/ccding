"""启动自动识别操作系统。

供 desktop（通知器）与 focus（前台检测）按平台选择具体实现，业务代码不直接写 if platform。
"""

import platform

from .log import get_logger

logger = get_logger(__name__)


def get_platform() -> str:
    """识别当前操作系统。

    功能说明：
        把 platform.system() 的结果归一化为内部短名，作为各平台工厂的分发依据。
    参数：
        无。
    返回值：
        'win'（Windows）/ 'mac'（macOS）/ 'other'（其它，仅日志、不弹本机窗口）。
    """
    system = platform.system()
    name = {"Windows": "win", "Darwin": "mac"}.get(system, "other")
    logger.info("识别到操作系统：%s -> %s", system, name)
    return name
