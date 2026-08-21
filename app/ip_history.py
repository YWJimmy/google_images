
"""
v3.3 IP历史池

职责:
- 保存出口IP历史
- 记录节点表现
- 支持冷却判断

默认使用本地json，后续可迁移database.py
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta


class IpHistoryStore:

    def __init__(self, path="private/ip_history.json"):
        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        if not self.path.exists():
            self.path.write_text(
                json.dumps(
                    {
                        "ips": {},
                        "nodes": {}
                    },
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )


    def _load(self):
        return json.loads(
            self.path.read_text(
                encoding="utf-8"
            )
        )


    def _save(self, data):
        self.path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )


    def record_ip(
        self,
        ip,
        node=None,
        group=None,
        status="ok"
    ):

        data = self._load()

        data["ips"][ip] = {
            "node": node,
            "group": group,
            "status": status,
            "updated":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }

        self._save(data)


    def is_cooling(
        self,
        ip,
        minutes=30
    ):

        data = self._load()

        item = data["ips"].get(ip)

        if not item:
            return False

        if item.get("status") != "challenge":
            return False

        updated = datetime.fromisoformat(
            item["updated"]
        )

        return (
            datetime.now(timezone.utc)
            - updated
            <
            timedelta(minutes=minutes)
        )
