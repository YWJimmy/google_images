from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Callable

from .clash_controller import ClashController, ClashControllerError, proxy_egress_identity


class CurrentChromeIpRotator:
    """Rotate the egress behind the fixed local proxy used by the current Chrome."""

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

    def rotate(
        self,
        group: str,
        *,
        settle_seconds: float = 1.5,
        delay_timeout_ms: int = 3000,
        max_candidates: int = 20,
    ) -> dict:
        if not 0 <= settle_seconds <= 10:
            raise ValueError("settle_seconds must be between 0 and 10")
        if not 500 <= delay_timeout_ms <= 10000:
            raise ValueError("delay_timeout_ms must be between 500 and 10000")
        if not 1 <= max_candidates <= 100:
            raise ValueError("max_candidates must be between 1 and 100")

        selectors = {item["group"]: item for item in self.controller.selectors()}
        if group not in selectors:
            raise ValueError("unknown Selector group")
        selector = selectors[group]
        original = str(selector.get("current", ""))
        if not original:
            raise ClashControllerError("current Selector node is empty")

        before = self.egress_check(self.proxy_url)
        before_fingerprint = str(before.get("ip_fingerprint", ""))
        if not before_fingerprint:
            raise ClashControllerError("egress check returned no IP fingerprint")

        probe = self.controller.probe_group_nodes(
            group, timeout_ms=int(delay_timeout_ms), max_nodes=500
        )
        direct_choices = set(str(item) for item in selector.get("choices", []))
        candidates = [
            str(item["name"])
            for item in probe.get("nodes", [])
            if item.get("usable")
            and str(item.get("name", "")) in direct_choices
            and str(item.get("name", "")) != original
        ][: int(max_candidates)]
        if not candidates:
            raise ClashControllerError("no alternative directly selectable usable node")

        attempts: list[dict] = []
        try:
            for node in candidates:
                attempt = {"node": node, "changed": False}
                try:
                    self.controller.switch(group, node)
                    self.controller.close_connections()
                    self.sleep(float(settle_seconds))
                    after = self.egress_check(self.proxy_url)
                    changed = str(after.get("ip_fingerprint", "")) != before_fingerprint
                    attempt.update(
                        {
                            "changed": changed,
                            "masked_ip": str(after.get("masked_ip", "")),
                            "latency_ms": after.get("latency_ms"),
                        }
                    )
                    attempts.append(attempt)
                    if changed:
                        return {
                            "ok": True,
                            "group": group,
                            "previous_node": original,
                            "current_node": node,
                            "previous_masked_ip": str(before.get("masked_ip", "")),
                            "current_masked_ip": str(after.get("masked_ip", "")),
                            "attempts": attempts,
                            "connections_reset": True,
                        }
                except Exception as exc:
                    attempt["error_type"] = type(exc).__name__
                    attempts.append(attempt)
        except BaseException:
            self._restore(group, original, settle_seconds)
            raise

        self._restore(group, original, settle_seconds)
        raise ClashControllerError("all usable candidate nodes kept the same egress or failed")

    def _restore(self, group: str, original: str, settle_seconds: float) -> None:
        self.controller.switch(group, original)
        self.controller.close_connections()
        self.sleep(float(settle_seconds))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Switch the current dedicated Chrome's Clash/Mihomo egress without restarting Chrome"
    )
    parser.add_argument("--group", help="Selector group; defaults to the group with most choices")
    parser.add_argument("--settle-seconds", type=float, default=1.5)
    parser.add_argument("--delay-timeout-ms", type=int, default=3000)
    parser.add_argument("--max-candidates", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    from .secret_store import DashboardSecretStore

    args = build_parser().parse_args(argv)
    root = Path.cwd().resolve()
    store = DashboardSecretStore(root / "private" / "dashboard_secrets.json")
    settings = store.public_settings()
    controller = ClashController(settings["clash_endpoint"], store.clash_secret())
    selectors = controller.selectors()
    if not selectors:
        raise SystemExit("No Selector groups are available")
    group = args.group or max(selectors, key=lambda item: len(item["choices"]))["group"]
    rotator = CurrentChromeIpRotator(controller, settings["clash_proxy_url"])
    try:
        result = rotator.rotate(
            group,
            settle_seconds=args.settle_seconds,
            delay_timeout_ms=args.delay_timeout_ms,
            max_candidates=args.max_candidates,
        )
    except (ClashControllerError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
