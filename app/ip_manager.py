
"""
v3.3 IP统一管理

新增:
- 完整IP内部判断
- 历史池
- 冷却机制
- 隐私显示分离
"""

from datetime import datetime, timezone

from .ip_history import IpHistoryStore


class IpRotationService:

    def __init__(
        self,
        rotator,
        history=None
    ):
        self.rotator = rotator

        self.history = (
            history
            or IpHistoryStore()
        )


    def current(self):
        return self.rotator.current()


    def rotate(self, **kwargs):

        result = self.rotator.rotate(
            **kwargs
        )

        result["timestamp"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        if result.get("ok"):

            new_ip = result.get(
                "new_ip",
                {}
            )

            full_ip = new_ip.get(
                "full_ip"
            )

            if full_ip:
                self.history.record_ip(
                    full_ip,
                    result.get("node"),
                    result.get("group")
                )

        return result


    def mark_challenge(
        self,
        ip,
        node=None
    ):

        self.history.record_ip(
            ip,
            node=node,
            status="challenge"
        )


    def success(self, result):
        return bool(
            isinstance(result, dict)
            and result.get("ok")
        )
