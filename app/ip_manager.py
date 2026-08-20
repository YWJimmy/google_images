
from __future__ import annotations

from datetime import datetime, timezone


class IpRotationService:
    """统一IP轮换入口。

    所有业务层应该通过这里调用IP切换，
    避免 runner/dashboard 等模块直接控制 Clash。
    """

    def __init__(self, rotator):
        self.rotator = rotator

    def rotate(self, group=None, **kwargs):
        result = self.rotator.rotate(group, **kwargs)
        result["timestamp"] = datetime.now(
            timezone.utc
        ).isoformat()
        return result

    @staticmethod
    def success(result):
        return bool(result.get("ok"))
