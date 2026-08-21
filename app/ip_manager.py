
"""
IP管理入口

整合:
- IP历史
- 节点评分
- 决策层
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

        if self.decision_engine:
            return self.rotator.rotate(
                decision_engine=self.decision_engine,
                **kwargs
            )

        result = self.rotator.rotate(
            **kwargs
        )

        result["timestamp"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        return result
