
"""
Node评分模块

v3.7接口修复版

提供统一接口:
- record_success()
- record_fail()
- record_challenge()
- get_score()

用于后续:
ip_rotator 节点选择
ip_manager 决策
dashboard 展示
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


class NodeScoreStore:

    def __init__(
        self,
        path="private/node_score.json"
    ):
        self.path = Path(path)

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        if not self.path.exists():
            self._save({})


    def _load(self):
        try:
            return json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            return {}


    def _save(self, data):
        self.path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )


    def _ensure(self, node):

        data = self._load()

        if node not in data:
            data[node] = {
                "success": 0,
                "fail": 0,
                "challenge": 0,
                "latencies": [],
                "updated": None
            }

        return data


    def record_success(
        self,
        node,
        latency_ms=None
    ):

        data = self._ensure(node)

        item = data[node]

        item["success"] += 1

        if latency_ms is not None:
            item["latencies"].append(
                latency_ms
            )

        item["updated"] = datetime.now(
            timezone.utc
        ).isoformat()

        self._save(data)


    def record_fail(
        self,
        node
    ):

        data = self._ensure(node)

        data[node]["fail"] += 1

        data[node]["updated"] = datetime.now(
            timezone.utc
        ).isoformat()

        self._save(data)


    def record_challenge(
        self,
        node
    ):

        data = self._ensure(node)

        data[node]["challenge"] += 1

        data[node]["updated"] = datetime.now(
            timezone.utc
        ).isoformat()

        self._save(data)


    def get_score(
        self,
        node
    ):

        data = self._load()

        item = data.get(
            node,
            {
                "success":0,
                "fail":0,
                "challenge":0,
                "latencies":[]
            }
        )

        success = item["success"]
        fail = item["fail"]
        challenge = item["challenge"]

        total = (
            success +
            fail +
            challenge
        )

        success_rate = (
            success / total
            if total
            else 0
        )

        latency_list = item.get(
            "latencies",
            []
        )

        avg_latency = (
            sum(latency_list) /
            len(latency_list)
            if latency_list
            else 0
        )

        score = (
            success_rate * 100
            - challenge * 5
            - fail * 2
        )

        if avg_latency:
            score -= min(
                avg_latency / 1000,
                20
            )

        return {
            "node": node,
            "success": success,
            "fail": fail,
            "challenge": challenge,
            "avg_latency_ms": round(
                avg_latency,
                2
            ),
            "score": round(
                max(score, 0),
                2
            )
        }
