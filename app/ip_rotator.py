
"""
v3 IP服务适配层

将 ClashController 暴露为 IpRotationService 可调用对象。
"""

from .clash_controller import (
    ClashController,
    proxy_egress_identity,
)


class ClashIpRotator:
    """
    IP轮换执行器。

    ip_manager.py 管理策略。
    本类负责实际执行。
    """

    def __init__(
        self,
        endpoint,
        secret,
        proxy_url,
    ):
        self.controller = ClashController(
            endpoint,
            secret
        )
        self.proxy_url = proxy_url

    def current(self, group="GLOBAL"):
        node = self.controller.get_current_node_v21(
            group
        )
        ip = proxy_egress_identity(
            self.proxy_url
        )

        return {
            "ok": bool(ip.get("ok")),
            "node": node,
            **ip,
        }

    def rotate(self, group="GLOBAL", **kwargs):
        nodes = self.controller.get_real_nodes_v21(
            group
        )

        old = self.current(group)

        for node in nodes:
            result = self.controller.switch_and_verify_v21(
                group,
                node["clash_name"]
            )

            if not result.get("ok"):
                continue

            new = self.current(group)

            if (
                new.get("ip_fingerprint")
                != old.get("ip_fingerprint")
            ):
                return {
                    "ok": True,
                    "old": old,
                    "new": new,
                    "node": node["clash_name"],
                }

        return {
            "ok": False,
            "error_code": "NO_IP_CHANGE",
            "old": old,
        }
