
"""
IP统一管理入口

职责:
- 管理IP轮换服务
- 将决策引擎传递给执行层
- 统一返回时间戳
"""

from datetime import datetime, timezone


class IpRotationService:

    def __init__(
        self,
        rotator,
        decision_engine=None
    ):
        self.rotator = rotator
        self.decision_engine = decision_engine


    def current(self):
        return self.rotator.current()


    def rotate(self, **kwargs):

        if (
            "decision_engine" not in kwargs
            and self.decision_engine is not None
        ):
            kwargs["decision_engine"] = self.decision_engine

        result = self.rotator.rotate(
            **kwargs
        )

        if isinstance(result, dict):
            result["timestamp"] = datetime.now(
                timezone.utc
            ).isoformat()

        return result
