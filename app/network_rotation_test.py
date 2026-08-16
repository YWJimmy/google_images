from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import time
from typing import Callable

from .clash_controller import ClashController, masked_proxy_egress
from .secret_store import DashboardSecretStore


def _bounded(value: int | float, minimum: int | float, maximum: int | float, name: str):
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


class NetworkRotationTester:
    """Rotate a local Clash Selector for bounded non-Google network diagnostics."""

    def __init__(
        self,
        controller: ClashController,
        proxy_url: str,
        *,
        egress_check: Callable[[str], dict] = masked_proxy_egress,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.controller = controller
        self.proxy_url = proxy_url
        self.egress_check = egress_check
        self.sleep = sleep

    def run(
        self,
        group: str,
        *,
        node_limit: int = 5,
        samples_per_node: int = 10,
        settle_seconds: float = 1,
        sample_interval_seconds: float = 0.25,
        delay_timeout_ms: int = 3000,
        progress: Callable[[str], None] | None = None,
    ) -> dict:
        _bounded(node_limit, 1, 20, "node_limit")
        _bounded(samples_per_node, 1, 50, "samples_per_node")
        _bounded(settle_seconds, 0, 10, "settle_seconds")
        _bounded(sample_interval_seconds, 0, 10, "sample_interval_seconds")
        _bounded(delay_timeout_ms, 500, 10000, "delay_timeout_ms")
        selectors = {item["group"]: item for item in self.controller.selectors()}
        if group not in selectors:
            raise ValueError("unknown Selector group")
        selector = selectors[group]
        original = str(selector.get("current", ""))
        choices = set(selector.get("choices", []))
        probe = self.controller.probe_group_nodes(
            group, timeout_ms=int(delay_timeout_ms), max_nodes=500
        )
        candidates = [
            item
            for item in probe["nodes"]
            if item.get("usable") and item.get("name") in choices
        ][: int(node_limit)]
        if not candidates:
            raise RuntimeError("no directly selectable usable nodes")
        results: list[dict] = []
        restored = False
        try:
            for index, candidate in enumerate(candidates, start=1):
                node = str(candidate["name"])
                if progress:
                    progress(f"[{index}/{len(candidates)}] switching and testing {node}")
                entry = {
                    "node": node,
                    "probe_delay_ms": candidate.get("delay_ms"),
                    "requested_samples": int(samples_per_node),
                    "successful_samples": 0,
                    "failed_samples": 0,
                    "masked_egresses": [],
                    "latency_ms": [],
                    "status": "unavailable",
                }
                try:
                    self.controller.switch(group, node)
                    self.sleep(float(settle_seconds))
                    masked_egresses: set[str] = set()
                    latencies: list[int] = []
                    for sample_index in range(int(samples_per_node)):
                        try:
                            sample = self.egress_check(self.proxy_url)
                            masked_egresses.add(str(sample["masked_ip"]))
                            latencies.append(int(sample["latency_ms"]))
                        except Exception:
                            entry["failed_samples"] += 1
                        if sample_index + 1 < int(samples_per_node):
                            self.sleep(float(sample_interval_seconds))
                    entry["successful_samples"] = len(latencies)
                    entry["masked_egresses"] = sorted(masked_egresses)
                    entry["latency_ms"] = latencies
                    if latencies:
                        entry.update(
                            {
                                "status": "usable",
                                "minimum_ms": min(latencies),
                                "median_ms": round(statistics.median(latencies)),
                                "average_ms": round(statistics.mean(latencies)),
                                "maximum_ms": max(latencies),
                            }
                        )
                except Exception as exc:
                    entry["error_type"] = type(exc).__name__
                results.append(entry)
        finally:
            if original:
                self.controller.switch(group, original)
                restored = True
        return {
            "mode": "pure_network_rotation",
            "tested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "group": group,
            "original_node": original,
            "original_restored": restored,
            "services": ["cp.cloudflare.com/generate_204", "api.ipify.org"],
            "google_accessed": False,
            "requested_nodes": int(node_limit),
            "tested_nodes": len(results),
            "usable_nodes": sum(1 for item in results if item["status"] == "usable"),
            "samples_per_node": int(samples_per_node),
            "results": results,
        }


def private_output_path(project_root: Path, value: str) -> Path:
    private_root = (project_root / "private").resolve()
    target = (project_root / value).resolve()
    if target != private_root and private_root not in target.parents:
        raise ValueError("output must stay inside the private directory")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded non-Google Clash node tests")
    parser.add_argument("--group", help="Selector group; defaults to the group with most choices")
    parser.add_argument("--nodes", type=int, default=5)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--settle-seconds", type=float, default=1)
    parser.add_argument("--sample-interval-seconds", type=float, default=0.25)
    parser.add_argument("--delay-timeout-ms", type=int, default=3000)
    parser.add_argument("--output", default="private/network_rotation_latest.json")
    parser.add_argument("--quiet", action="store_true", help="hide private node names from stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd().resolve()
    store = DashboardSecretStore(root / "private" / "dashboard_secrets.json")
    settings = store.public_settings()
    controller = ClashController(settings["clash_endpoint"], store.clash_secret())
    selectors = controller.selectors()
    if not selectors:
        raise SystemExit("No Selector groups are available")
    group = args.group or max(selectors, key=lambda item: len(item["choices"]))["group"]
    tester = NetworkRotationTester(controller, settings["clash_proxy_url"])
    result = tester.run(
        group,
        node_limit=args.nodes,
        samples_per_node=args.samples,
        settle_seconds=args.settle_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        delay_timeout_ms=args.delay_timeout_ms,
        progress=None if args.quiet else lambda message: print(message, flush=True),
    )
    output = private_output_path(root, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(
        json.dumps(
            {
                "tested_nodes": result["tested_nodes"],
                "usable_nodes": result["usable_nodes"],
                "original_restored": result["original_restored"],
                "output": str(output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
