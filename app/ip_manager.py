
"""
ip_manager.py v3.1

统一IP服务入口。

原则：
- 不直接操作Clash
- 不因为单次IP失败停止任务
- 返回结构化状态
"""


from datetime import datetime, timezone


class IpRotationService:

    def __init__(self, rotator):
        self.rotator = rotator


    def current(self, **kwargs):
        return self.rotator.current(
            **kwargs
        )


    def rotate(self, group=None, **kwargs):

        result = self.rotator.rotate(
            group=group,
            **kwargs
        )

        result["timestamp"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        return result


    def success(self, result):

        return bool(
            isinstance(result, dict)
            and result.get("ok")
        )


    def should_retry(self, result):

        if not isinstance(result, dict):
            return False

        return result.get(
            "error_code"
        ) in {
            "ALL_NODE_FAILED",
            "EGRESS_CHECK_FAILED",
            "NO_IP_CHANGE",
        }
