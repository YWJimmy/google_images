"""Helpers for identifying nodes that share the same public egress."""

from __future__ import annotations

import hashlib
from typing import Iterable


def cluster_id(full_ip: str) -> str:
    return "ipg_" + hashlib.sha256(full_ip.encode("ascii")).hexdigest()[:12]


def build_ip_clusters(rows: Iterable[dict]) -> list[dict]:
    groups: dict[str, set[str]] = {}
    for row in rows:
        full_ip = row.get("full_ip")
        node = row.get("name") or row.get("node")
        if full_ip and node:
            groups.setdefault(str(full_ip), set()).add(str(node))
    return [
        {
            "cluster_id": cluster_id(full_ip),
            "full_ip": full_ip,
            "nodes": sorted(nodes),
            "shared": len(nodes) > 1,
        }
        for full_ip, nodes in groups.items()
    ]

