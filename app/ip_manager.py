
from __future__ import annotations

from datetime import datetime, timezone


class IpRotationService:
    """
    V3 IP统一管理层。

    架构职责：
    runner/google_images 不直接控制 Clash。
    所有IP相关动作经过本服务。

    负责：
    - 获取当前出口状态
    - 触发IP轮换
    - 记录轮换结果
    - 提供统一成功判断

    不负责：
    - Google搜索逻辑
    - 浏览器控制
    - Clash底层HTTP细节
    """

    def __init__(self, rotator):
        self.rotator = rotator

    def current(self, **kwargs):
        """获取当前IP状态。"""
        if hasattr(self.rotator, "current"):
            return self.rotator.current(**kwargs)

        return {
            "ok": False,
            "error_code": "CURRENT_IP_NOT_SUPPORTED"
        }

    def rotate(self, group=None, **kwargs):
        """
        执行一次IP轮换。

        注意：
        失败不应直接导致系统停机。
        上层根据返回状态决定：
        - 重试
        - 跳过
        - 继续任务
        """

        result = self.rotator.rotate(
            group,
            **kwargs
        )

        result["timestamp"] = (
            datetime.now(timezone.utc)
            .isoformat()
        )

        return result

    @staticmethod
    def success(result):
        return bool(
            isinstance(result, dict)
            and result.get("ok")
        )

    @staticmethod
    def should_retry(result):
        """
        判断是否值得继续尝试。

        网络瞬态问题：
        可以继续。

        配置错误：
        不应无限重试。
        """

        if not isinstance(result, dict):
            return False

        return result.get(
            "error_code"
        ) in {
            "NO_IP_CHANGE",
            "SWITCH_TIMEOUT",
            "TEMP_NETWORK_ERROR",
        }
