
"""
ip_rotator.py v3.1

IP执行层：
- 调用 ClashController
- 执行节点切换
- 验证出口IP

v3.1改进：
1. 单节点失败不会导致程序停止
2. 记录每次尝试状态
3. 所有节点失败后统一返回错误
4. 增加切换稳定等待
"""

from __future__ import annotations

import time

from .clash_controller import (
    ClashController,
    proxy_egress_identity,
)


class ClashIpRotator:

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
        """
        获取当前节点和出口IP。

        任何检测失败均返回状态，
        不直接抛出异常。
        """

        try:
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

        except Exception as e:

            return {
                "ok": False,
                "error_code":
                    "EGRESS_CHECK_FAILED",
                "reason": str(e),
            }


    def rotate(
        self,
        group="GLOBAL",
        wait_after_switch=3,
        **kwargs
    ):

        attempts = []

        old = self.current(group)

        nodes = self.controller.get_real_nodes_v21(
            group
        )

        for node in nodes:

            name = node["clash_name"]

            attempt = {
                "node": name,
                "type": node.get("type"),
            }

            try:

                switched = (
                    self.controller
                    .switch_and_verify_v21(
                        group,
                        name,
                        wait=wait_after_switch
                    )
                )

                if not switched.get("ok"):

                    attempt["reason"] = (
                        "switch_not_confirmed"
                    )
                    attempts.append(attempt)
                    continue


                new = self.current(group)


                if not new.get("ok"):

                    attempt["reason"] = (
                        "egress_check_failed"
                    )
                    attempts.append(attempt)
                    continue


                attempt["masked_ip"] = (
                    new.get("masked_ip")
                )


                attempts.append(attempt)


                if (
                    old.get("ip_fingerprint")
                    != new.get("ip_fingerprint")
                ):

                    return {
                        "ok": True,
                        "old": old,
                        "new": new,
                        "node": name,
                        "attempts": attempts,
                    }


                attempt["reason"] = (
                    "same_egress"
                )

            except Exception as e:

                attempt["reason"] = str(e)


            attempts.append(attempt)


        return {
            "ok": False,
            "error_code":
                "ALL_NODE_FAILED",
            "old": old,
            "attempts": attempts,
        }
