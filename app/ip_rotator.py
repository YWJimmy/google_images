
"""
IP执行层

职责:
- 获取候选节点
- 使用决策层排序
- 执行Clash切换
- 更新节点评分

"""

from .clash_route_detector import ClashRouteDetector
from .clash_controller import proxy_egress_identity


class ClashIpRotator:

    def __init__(self, endpoint, secret, proxy_url,
                 decision_engine=None):
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

        self.decision_engine = decision_engine


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


    def rotate(self, group=None, wait_after_switch=3, decision_engine=None):

        attempts = []
        engine = decision_engine or self.decision_engine
        decision = None

        if group is None:
            detected = self.detector.find_active_group()

            if not detected:
                return {
                    "ok": False,
                    "error_code":"NO_ACTIVE_GROUP"
                }

            group = detected["group"]


        old = proxy_egress_identity(
            self.proxy_url
        )


        nodes = self.controller.get_real_nodes_v21(
            group
        )


        # v3.8: 决策层作为唯一节点选择来源
        #
        # 注意:
        # engine.choose() 返回的是已经完成风险过滤后的结果。
        # 后续禁止重新排序、随机选择或重新调用节点发现逻辑。
        # rotate() 只能执行 decision.selected 指定的节点。
        if engine:
            decision = engine.choose(nodes)

            selected = decision.get("selected")

            if not selected:
                return {
                    "ok": False,
                    "error_code": "NO_USABLE_NODE",
                    "group": group,
                    "decision": decision
                }

            selected_node = selected.get("node")

            if not selected_node:
                return {
                    "ok": False,
                    "error_code": "INVALID_DECISION_RESULT",
                    "group": group,
                    "decision": decision
                }

            # 唯一目标节点
            nodes = [selected_node]

            print({
                "decision_selected": selected_node.get("clash_name"),
                "switch_target": selected_node.get("clash_name")
            })


        for node in nodes:

            name = (
                node["clash_name"]
                if isinstance(node, dict)
                else node
            )

            try:

                switched = self.controller.switch_and_verify_v21(
                    group,
                    name,
                    wait=wait_after_switch
                )

                if not switched.get("ok"):
                    attempts.append({
                        "node":name,
                        "reason":"switch_failed"
                    })
                    continue


                new = proxy_egress_identity(
                    self.proxy_url
                )


                if new.get("ip_fingerprint") != old.get(
                    "ip_fingerprint"
                ):

                    if engine:
                        engine.score_store.record_success(
                            name,
                            new.get("latency_ms")
                        )

                    return {
                        "ok":True,
                        "group":group,
                        "node":name,
                        "old_ip":old,
                        "new_ip":new,
                        "attempts":attempts,
                        "decision":decision
                    }


                attempts.append({
                    "node":name,
                    "reason":"same_egress"
                })


            except Exception as e:

                attempts.append({
                    "node":name,
                    "reason":str(e)
                })

                if engine:
                    engine.score_store.record_fail(
                        name
                    )


        return {
            "ok":False,
            "error_code":"ALL_NODE_FAILED",
            "group":group,
            "attempts":attempts,
            "decision":decision
        }
