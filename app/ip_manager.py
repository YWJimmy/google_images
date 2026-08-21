"""
v3.2 IP统一管理入口
"""

from datetime import datetime, timezone


class IpRotationService:
    def __init__(self, rotator):
        self.rotator = rotator

    def current(self):
        return self.rotator.current()

    def rotate(self, **kwargs):
        result = self.rotator.rotate(**kwargs)
        result["timestamp"] = datetime.now(
            timezone.utc
        ).isoformat()
        return result

    def success(self, result):
        return isinstance(result, dict) and result.get("ok")
