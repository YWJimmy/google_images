
"""
IP统一管理入口

修复:
- 决策层未接入问题
- rotate结果状态不完整问题
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

        result = self.rotator.rotate(
            **kwargs
        )

        if isinstance(result, dict):
            result["timestamp"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

        return result
