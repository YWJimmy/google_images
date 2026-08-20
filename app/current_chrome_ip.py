"""
current_chrome_ip.py v2.1

功能：
1. 调用 ClashController v2.1 获取真实出口节点
2. 使用 Clash 返回的完整节点名称进行切换
3. 验证 Selector 是否真正切换成功
4. 通过 Clash 代理端口检测公网出口 IP

说明：
- 不修改原有搜索逻辑
- 作为独立 IP 切换测试入口
"""

from __future__ import annotations

import argparse
import json
import time

from app.clash_controller import (
    ClashController,
    proxy_egress_identity,
)


def masked(value: str | None):
    """隐藏IP后两段，方便日志显示"""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".xxx"
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--group",
        default="GLOBAL",
        help="Clash Selector group name"
    )
    parser.add_argument(
        "--proxy",
        default="http://127.0.0.1:7897",
        help="Clash proxy url"
    )
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:9097",
        help="Clash API endpoint"
    )
    parser.add_argument(
        "--secret",
        default="set-your-secret",
        help="Clash API secret"
    )

    args = parser.parse_args()

    controller = ClashController(
        args.endpoint,
        args.secret
    )

    result = {
        "ok": False,
        "group": args.group,
        "attempts": []
    }

    previous_node = controller.get_current_node_v21(
        args.group
    )

    previous_ip = proxy_egress_identity(
        args.proxy
    )

    result["previous_node"] = previous_node
    result["previous_ip"] = (
        previous_ip.get("masked_ip")
        if previous_ip.get("ok")
        else None
    )

    nodes = controller.get_real_nodes_v21(
        args.group
    )

    for node in nodes:

        clash_name = node["clash_name"]

        attempt = {
            "node": clash_name,
            "type": node.get("type")
        }

        try:

            switched = controller.switch_and_verify_v21(
                args.group,
                clash_name
            )

            if not switched.get("ok"):
                attempt["reason"] = "switch_not_confirmed"
                result["attempts"].append(attempt)
                continue


            time.sleep(1)


            current_ip = proxy_egress_identity(
                args.proxy
            )

            attempt["masked_ip"] = (
                current_ip.get("masked_ip")
                if current_ip.get("ok")
                else None
            )

            result["attempts"].append(attempt)


            old_fp = previous_ip.get(
                "ip_fingerprint"
            )

            new_fp = current_ip.get(
                "ip_fingerprint"
            )


            if (
                current_ip.get("ok")
                and new_fp != old_fp
            ):

                result.update(
                    {
                        "ok": True,
                        "current_node": clash_name,
                        "current_ip":
                            current_ip.get(
                                "masked_ip"
                            )
                    }
                )

                print(
                    json.dumps(
                        result,
                        ensure_ascii=False,
                        indent=2
                    )
                )
                return


        except Exception as e:
            attempt["reason"] = str(e)
            result["attempts"].append(attempt)


    result["error_code"] = "NO_IP_CHANGE"

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )


if __name__ == "__main__":
    main()
