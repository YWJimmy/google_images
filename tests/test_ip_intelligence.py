import sqlite3
from pathlib import Path
import uuid

from app.database import IpIntelligenceDatabase
from app.full_ip import FullIpCollector, mask_full_ip
from app.ip_cluster import build_ip_clusters
from app.ip_identity import IpIdentityService
from app.ip_reputation import IpReputationService
from app.ip_verifier import ChromeIpVerifier, FullIpVerifier


def database_path() -> Path:
    return Path("private") / f"test_ip_intelligence_{uuid.uuid4().hex}.sqlite3"


def test_full_ip_collector_uses_provider_consensus():
    responses = {
        "ipify": '{"ip":"203.0.113.10"}',
        "ifconfig.me": "203.0.113.10\n",
        "ipinfo": '{"ip":"198.51.100.8","country":"JP"}',
    }
    collector = FullIpCollector(
        fetcher=lambda url, _proxy, _timeout: next(
            value for key, value in responses.items() if key in url
        )
    )
    result = collector.collect("http://127.0.0.1:7897", minimum_consensus=2)
    assert result["ok"] is True
    assert result["full_ip"] == "203.0.113.10"
    assert result["consensus"] == 2
    assert mask_full_ip(result["full_ip"]) == "203.0.xxx.xxx"


def test_full_ip_collector_rejects_disagreeing_valid_providers():
    collector = FullIpCollector(
        fetcher=lambda url, _proxy, _timeout: (
            '{"ip":"203.0.113.10"}' if "ipify" in url
            else ('198.51.100.20' if "ifconfig" in url
                  else '{"ip":"192.0.2.30"}')
        )
    )
    result = collector.collect("http://127.0.0.1:7897")
    assert result["ok"] is False
    assert result["error_code"] == "IP_PROVIDER_MISMATCH"


def test_identity_database_tracks_mapping_cluster_and_stability():
    database = IpIntelligenceDatabase(database_path())
    identity = IpIdentityService(database)
    identity.observe("Hong Kong 01", "203.0.113.10", country="HK")
    identity.observe("Hong Kong 02", "203.0.113.10", country="HK")
    identity.observe("Hong Kong 01", "198.51.100.20", country="JP")

    latest = identity.for_node("Hong Kong 01", reveal_full_ip=True)
    assert latest["full_ip"] == "198.51.100.20"
    assert latest["stability"]["ip_change_rate"] == 1.0
    old_cluster = next(
        item for item in build_ip_clusters(database.list_node_intelligence())
        if item["full_ip"] == "203.0.113.10"
    )
    assert old_cluster["nodes"] == ["Hong Kong 02"]


def test_reputation_challenge_enters_cooling():
    database = IpIntelligenceDatabase(database_path())
    database.observe_ip("Node A", "203.0.113.10")
    reputation = IpReputationService(database, challenge_cooling_minutes=30)
    reputation.mark_challenge("203.0.113.10", node="Node A")
    current = database.latest_ip_for_node("Node A")
    assert reputation.effective_state(current) == "COOLING"
    assert reputation.score(current) < 50


def test_same_egress_cools_the_shared_ip_identity():
    database = IpIntelligenceDatabase(database_path())
    database.observe_ip("Node A", "203.0.113.10")
    reputation = IpReputationService(database)
    reputation.mark_same_egress("203.0.113.10", node="Node A")
    assert database.latest_ip_for_node("Node A")["status"] == "COOLING"


def test_rotation_log_is_idempotent_per_attempt():
    path = database_path()
    database = IpIntelligenceDatabase(path)
    database.record_rotation("r1", 1, node="A", decision_node="A",
                             full_ip=None, result="failed", state="FAILED")
    database.record_rotation("r1", 1, node="A", decision_node="A",
                             full_ip="203.0.113.10", result="success", state="SUCCESS")
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT result, full_ip FROM rotation_log").fetchall()
    assert rows == [("success", "203.0.113.10")]


def test_proxy_and_chrome_verifiers_report_real_change():
    class Collector:
        def collect(self, _proxy):
            return {"ok": True, "full_ip": "203.0.113.11"}

    assert FullIpVerifier(Collector()).verify_change(
        "http://127.0.0.1:7897", "203.0.113.10"
    )["changed"] is True
    assert ChromeIpVerifier(lambda _endpoint: "203.0.113.11").verify(
        "http://127.0.0.1:9222", "203.0.113.11"
    )["matches_expected"] is True
