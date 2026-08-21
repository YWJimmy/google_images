
"""
IP智能管理入口

负责:
- 调用IP轮换
- 传递决策引擎
- 统一状态返回
"""
from datetime import datetime, timezone

class IpRotationService:

    def __init__(self, rotator, decision_engine=None, history=None):
        self.rotator = rotator
        self.decision_engine = decision_engine
        self.history = history


    def current(self):
        return self.rotator.current()


    def rotate(self, **kwargs):
        if self.decision_engine and "decision_engine" not in kwargs:
            kwargs["decision_engine"] = self.decision_engine

        result = self.rotator.rotate(**kwargs)

        if isinstance(result, dict):
            result["timestamp"] = datetime.now(timezone.utc).isoformat()

        return result


    def mark_challenge(self, ip, node=None, group=None):
        if self.history:
            self.history.record_ip(
                ip,
                node=node,
                group=group,
                status="challenge"
            )
