
"""
IP智能管理入口

职责:
- 管理IP轮换
- 保证整个生命周期使用同一个decision_engine实例
- 管理history状态
"""

from datetime import datetime, timezone


class IpRotationService:

    def __init__(self, rotator, decision_engine=None, history=None):
        self.rotator = rotator
        self.decision_engine = decision_engine
        self.history = history

        # 关键修复:
        # manager创建时立即绑定执行层
        if decision_engine is not None:
            self.rotator.decision_engine = decision_engine


    def current(self):
        return self.rotator.current()


    def rotate(self, **kwargs):

        # 强制使用当前manager持有的决策实例
        if self.decision_engine is not None:
            kwargs["decision_engine"] = self.decision_engine

        result = self.rotator.rotate(
            **kwargs
        )

        if isinstance(result, dict):
            result["timestamp"] = datetime.now(
                timezone.utc
            ).isoformat()

        return result


    def mark_challenge(self, ip, node=None, group=None):
        if self.history:
            self.history.record_ip(
                ip,
                node=node,
                group=group,
                status="challenge"
            )
