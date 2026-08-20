
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

from .clash_controller import (
    ClashController,
    ClashControllerError,
    proxy_egress_identity,
)


# 不参与真实出口切换的策略名称
NON_LEAF_NAMES = {
    "DIRECT",
    "REJECT",
    "自动选择",
    "故障转移",
    "故障转移",
}


class CurrentChromeIpRotator:
    """
    v2:
    Chrome固定代理出口IP轮换器。

    改进:
    1. 不再简单遍历 Selector choices
    2. 支持 GLOBAL 中嵌套策略组
    3. 过滤策略组和无效节点
    4. 输出详细失败原因
    """

    def __init__(
        self,
        controller: ClashController,
        proxy_url: str,
        *,
        egress_check: Callable[[str], dict] = proxy_egress_identity,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.controller = controller
        self.proxy_url = proxy_url
        self.egress_check = egress_check
        self.sleep = sleep

    def _candidate_nodes(self, group: str) -> list[str]:
        """
        获取真实可切换节点。
        不直接使用 Selector 的 all，
        而使用 ClashController 展开的叶子节点测速结果。
        """
        probe = self.controller.probe_group_nodes(
            group,
            timeout_ms=3000,
            max_nodes=500,
        )

        result = []
        for item in probe.get("nodes", []):
            name = str(item.get("name", ""))
            if not name:
                continue
            if name in NON_LEAF_NAMES:
                continue
            if not item.get("usable"):
                continue
            result.append(name)

        return result

    def rotate(
        self,
        group: str,
        *,
        settle_seconds: float = 2,
        max_candidates: int = 20,
    ) -> dict:

        selectors = {
            item["group"]: item
            for item in self.controller.selectors()
        }

        if group not in selectors:
            return {
                "ok": False,
                "error_code": "GROUP_NOT_FOUND",
                "group": group,
            }

        selector = selectors[group]

        before = self.egress_check(self.proxy_url)
        before_fp = str(before.get("ip_fingerprint", ""))

        if not before_fp:
            return {
                "ok": False,
                "error_code": "IP_CHECK_FAILED",
            }

        original = selector.get("current_leaf") or selector.get("current")

        candidates = [
            x for x in self._candidate_nodes(group)
            if x != original
        ][:max_candidates]

        if not candidates:
            return {
                "ok": False,
                "error_code": "NO_USABLE_NODE",
                "group": group,
            }

        attempts = []

        for node in candidates:
            attempt = {
                "node": node,
            }

            try:
                self.controller.switch(group, node)
                self.controller.close_connections()
                self.sleep(settle_seconds)

                after = self.egress_check(self.proxy_url)

                changed = (
                    str(after.get("ip_fingerprint", ""))
                    != before_fp
                )

                attempt.update({
                    "changed": changed,
                    "masked_ip": after.get("masked_ip"),
                    "latency_ms": after.get("latency_ms"),
                })

                attempts.append(attempt)

                if changed:
                    return {
                        "ok": True,
                        "group": group,
                        "previous_node": original,
                        "current_node": node,
                        "previous_masked_ip":
                            before.get("masked_ip"),
                        "current_masked_ip":
                            after.get("masked_ip"),
                        "attempts": attempts,
                        "connections_reset": True,
                    }

            except Exception as exc:
                attempt.update({
                    "changed": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                attempts.append(attempt)

        return {
            "ok": False,
            "error_code": "NO_IP_CHANGE",
            "group": group,
            "attempts": attempts,
        }


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default="GLOBAL")
    parser.add_argument("--max-candidates", type=int, default=20)
    return parser


def main(argv=None):
    from .secret_store import DashboardSecretStore

    args = build_parser().parse_args(argv)

    root = Path.cwd().resolve()

    store = DashboardSecretStore(
        root / "private" / "dashboard_secrets.json"
    )

    settings = store.public_settings()

    controller = ClashController(
        settings["clash_endpoint"],
        store.clash_secret(),
    )

    rotator = CurrentChromeIpRotator(
        controller,
        settings["clash_proxy_url"],
    )

    result = rotator.rotate(
        args.group,
        max_candidates=args.max_candidates,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
