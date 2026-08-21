"""IP-driven node decision engine with legacy history compatibility."""

from __future__ import annotations

from typing import Any, Iterable


RISK_STATUSES = {"challenge", "same_egress", "cooling", "blocked"}


class IpDecisionEngine:
    """Rank nodes by node quality and the latest known public-IP identity."""

    def __init__(self, history, score_store, identity_service=None):
        self.history = history
        self.score_store = score_store
        self.identity_service = identity_service

    def _legacy_status(self, node: str) -> str | None:
        getter = getattr(self.history, "get_node_status", None)
        if callable(getter):
            return getter(node)
        loader = getattr(self.history, "_load", None)
        if not callable(loader):
            return None
        data = loader()
        node_info = data.get("nodes", {}).get(node)
        if node_info and node_info.get("status"):
            return str(node_info["status"])
        for item in data.get("ips", {}).values():
            if item.get("node") == node:
                return item.get("status")
        return None

    def _identity(self, node: str) -> dict[str, Any] | None:
        if self.identity_service is None:
            return None
        return self.identity_service.for_node(node, reveal_full_ip=True)

    @staticmethod
    def _success_rate(score: dict[str, Any]) -> float:
        success = int(score.get("success") or 0)
        failure = int(score.get("fail") or 0)
        challenge = int(score.get("challenge") or 0)
        total = success + failure + challenge
        return success / total * 100 if total else 50.0

    def rank_nodes(self, nodes: Iterable[dict], *,
                   exclude_nodes: Iterable[str] | None = None,
                   exclude_ips: Iterable[str] | None = None) -> list[dict[str, Any]]:
        excluded_nodes = set(exclude_nodes or ())
        excluded_ips = set(exclude_ips or ())
        ranked: list[dict[str, Any]] = []
        for node in nodes:
            name = str(node["clash_name"])
            if name in excluded_nodes:
                continue
            node_score = self.score_store.get_score(name)
            identity = self._identity(name)
            full_ip = identity.get("full_ip") if identity else None
            if full_ip and full_ip in excluded_ips:
                continue
            node_quality = float(node_score.get("score") or 0)
            success_rate = self._success_rate(node_score)
            ip_score = float(identity.get("ip_score") if identity else 50)
            stability = identity.get("stability", {}) if identity else {}
            history_score = 50 + 50 * float(stability.get("ip_change_rate") or 0)
            final_score = (
                node_quality * 0.40
                + ip_score * 0.35
                + history_score * 0.15
                + success_rate * 0.10
            )
            ranked.append({
                "node": node,
                "score": round(final_score, 2),
                "full_ip": full_ip,
                "ip_score": round(ip_score, 2),
                "ip_cluster": identity.get("cluster_id") if identity else None,
                "components": {
                    "node_quality": round(node_quality, 2),
                    "ip_reputation": round(ip_score, 2),
                    "history": round(history_score, 2),
                    "success_rate": round(success_rate, 2),
                },
            })
        ranked.sort(key=lambda item: (-item["score"], item["node"]["clash_name"]))
        return ranked

    def choose(self, nodes: Iterable[dict], *,
               exclude_nodes: Iterable[str] | None = None,
               exclude_ips: Iterable[str] | None = None) -> dict[str, Any]:
        ranked = self.rank_nodes(
            nodes, exclude_nodes=exclude_nodes, exclude_ips=exclude_ips
        )
        usable: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen_clusters: set[str] = set()
        for item in ranked:
            name = str(item["node"]["clash_name"])
            legacy_status = self._legacy_status(name)
            identity = self._identity(name)
            ip_state = str(identity.get("status") or "ACTIVE").upper() if identity else "ACTIVE"
            if legacy_status in RISK_STATUSES:
                skipped.append({"node": name, "reason": legacy_status})
                continue
            if ip_state in {"COOLING", "BLOCKED"}:
                skipped.append({
                    "node": name,
                    "reason": "ip_cooling" if ip_state == "COOLING" else "ip_blocked",
                    "full_ip": item.get("full_ip"),
                })
                continue
            cluster = item.get("ip_cluster")
            if cluster and cluster in seen_clusters:
                skipped.append({
                    "node": name,
                    "reason": "duplicate_ip_cluster",
                    "full_ip": item.get("full_ip"),
                })
                continue
            if cluster:
                seen_clusters.add(cluster)
            usable.append(item)
        return {
            "selected": usable[0] if usable else None,
            "usable": usable,
            "skipped": skipped,
            "strategy": "ip_driven_v3.9",
        }

    def feedback(self, node: str, result: str, *, full_ip: str | None = None,
                 group: str | None = None, latency_ms: float | None = None) -> None:
        normalized = result.lower()
        if normalized == "success":
            self.score_store.record_success(node, latency_ms)
        else:
            self.score_store.record_fail(node)
        if normalized == "same_egress":
            marker = getattr(self.history, "mark_same_egress", None)
            if callable(marker) and full_ip:
                marker(full_ip, node=node, group=group)
        service = self.identity_service
        database = getattr(service, "database", None)
        if database is not None and full_ip:
            database.record_event(
                normalized, full_ip=full_ip, node=node, details={"group": group}
            )
