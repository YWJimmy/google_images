"""Node-to-IP identity, stability and cluster analysis."""

from __future__ import annotations

from typing import Any

from .database import IpIntelligenceDatabase
from .full_ip import mask_full_ip
from .ip_cluster import build_ip_clusters, cluster_id
from .ip_reputation import IpReputationService


class IpIdentityService:
    def __init__(self, database: IpIntelligenceDatabase,
                 reputation: IpReputationService | None = None):
        self.database = database
        self.reputation = reputation or IpReputationService(database)

    def observe(self, node: str, full_ip: str, *, country: str | None = None,
                node_type: str | None = None) -> dict[str, Any]:
        self.database.observe_ip(
            node, full_ip, country=country, node_type=node_type
        )
        return self.for_node(node, reveal_full_ip=True) or {}

    def stability(self, node: str, limit: int = 100) -> dict[str, Any]:
        chronological = list(reversed(self.database.node_history(node, limit)))
        ips = [str(item["full_ip"]) for item in chronological]
        transitions = max(0, len(ips) - 1)
        changes = sum(1 for left, right in zip(ips, ips[1:]) if left != right)
        unique = len(set(ips))
        return {
            "observations": len(ips),
            "unique_ips": unique,
            "ip_change_rate": round(changes / transitions, 4) if transitions else 0.0,
            "ip_reuse_rate": round(1 - unique / len(ips), 4) if ips else 0.0,
        }

    def for_node(self, node: str, *, reveal_full_ip: bool = False) -> dict[str, Any] | None:
        identity = self.database.latest_ip_for_node(node)
        if not identity:
            return None
        full_ip = str(identity["full_ip"])
        peers = self.database.nodes_for_ip(full_ip)
        result = dict(identity)
        result.update({
            "node": node,
            "masked_ip": mask_full_ip(full_ip),
            "cluster_id": cluster_id(full_ip),
            "cluster_nodes": peers,
            "shared_egress": len(peers) > 1,
            "status": self.reputation.effective_state(identity),
            "ip_score": self.reputation.score(identity),
            "stability": self.stability(node),
        })
        if not reveal_full_ip:
            result.pop("full_ip", None)
        return result

    def dashboard_rows(self) -> list[dict[str, Any]]:
        rows = self.database.list_node_intelligence()
        clusters = {item["full_ip"]: item for item in build_ip_clusters(rows)}
        output = []
        for row in rows:
            full_ip = row.get("full_ip")
            identity = dict(row)
            identity["full_ip"] = full_ip
            cluster = clusters.get(full_ip, {}) if full_ip else {}
            success = int(row.get("success") or 0)
            failure = int(row.get("failure") or 0)
            challenge = int(row.get("challenge") or 0)
            total = success + failure + challenge
            output.append({
                "node": row["name"],
                "type": row.get("type"),
                "country": row.get("country") or row.get("node_country"),
                "masked_ip": mask_full_ip(full_ip) if full_ip else None,
                "status": self.reputation.effective_state(identity),
                "ip_score": self.reputation.score(identity),
                "success": success,
                "failure": failure,
                "challenge": challenge,
                "success_rate": round(success / total * 100, 1) if total else None,
                "last_challenge": row.get("last_challenge"),
                "last_seen": row.get("observed_at"),
                "cluster_id": cluster.get("cluster_id"),
                "shared_egress": cluster.get("shared", False),
            })
        return output
