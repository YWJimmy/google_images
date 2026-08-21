"""
v3.2 IP执行层

特点：
- 不固定GLOBAL
- 自动寻找出口Selector
- 保留失败记录
"""

from .clash_route_detector import ClashRouteDetector
from .clash_controller import proxy_egress_identity


class ClashIpRotator:

    def __init__(self, endpoint, secret, proxy_url):
        from .clash_controller import ClashController

        self.controller = ClashController(
            endpoint,
            secret
        )
        self.proxy_url = proxy_url

        self.detector = ClashRouteDetector(
            self.controller,
            proxy_url
        )

    def current(self):
        group = self.detector.find_active_group()

        ip = proxy_egress_identity(
            self.proxy_url
        )

        return {
            "ok": ip.get("ok"),
            "group": group,
            "ip": ip
        }

    def rotate(self, group=None, wait_after_switch=3):
        attempts = []

        if group is None:
            detected = self.detector.find_active_group()

            if not detected:
                return {
                    "ok": False,
                    "error_code":
                    "NO_ACTIVE_GROUP"
                }

            group = detected["group"]

        old = proxy_egress_identity(
            self.proxy_url
        )

        nodes = self.controller.get_real_nodes_v21(
            group
        )

        for node in nodes:
            name = node["clash_name"]

            try:
                switched = self.controller.switch_and_verify_v21(
                    group,
                    name,
                    wait=wait_after_switch
                )

                if not switched.get("ok"):
                    attempts.append({
                        "node": name,
                        "reason": "switch_failed"
                    })
                    continue

                new = proxy_egress_identity(
                    self.proxy_url
                )

                if new.get("ip_fingerprint") != old.get(
                    "ip_fingerprint"
                ):
                    return {
                        "ok": True,
                        "group": group,
                        "node": name,
                        "old_ip": old,
                        "new_ip": new,
                        "attempts": attempts
                    }

                attempts.append({
                    "node": name,
                    "reason": "same_egress"
                })

            except Exception as e:
                attempts.append({
                    "node": name,
                    "reason": str(e)
                })

        return {
            "ok": False,
            "error_code":
            "ALL_NODE_FAILED",
            "group": group,
            "attempts": attempts
        }
