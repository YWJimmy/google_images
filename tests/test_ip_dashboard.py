from pathlib import Path
import threading
import uuid

from app.database import IpIntelligenceDatabase
from app.dashboard import DashboardManager
from app.ip_identity import IpIdentityService
from app.ip_reputation import IpReputationService


def manager_with_database():
    manager = DashboardManager.__new__(DashboardManager)
    manager.root = Path.cwd()
    manager.lock = threading.RLock()
    manager.clash_context = {"clash_group": None, "clash_node": None}
    manager.ip_rotation_lock = threading.Lock()
    manager.ip_database = IpIntelligenceDatabase(
        Path("private") / f"test_dashboard_ip_{uuid.uuid4().hex}.sqlite3"
    )
    manager.ip_reputation = IpReputationService(manager.ip_database)
    manager.ip_identity_service = IpIdentityService(
        manager.ip_database, manager.ip_reputation
    )
    return manager


def test_dashboard_returns_only_masked_ip_and_challenge_cooling():
    manager = manager_with_database()
    manager.ip_identity_service.observe("Node A", "203.0.113.10", country="SG")
    manager.record_node_challenge("Node A")
    rows = manager.ip_intelligence()
    assert rows[0]["masked_ip"] == "203.0.xxx.xxx"
    assert "full_ip" not in rows[0]
    assert rows[0]["status"] == "COOLING"
    assert rows[0]["challenge"] == 1
    assert rows[0]["last_challenge"]


def test_manual_egress_check_persists_current_node_without_exposing_full_ip(monkeypatch):
    manager = manager_with_database()
    manager.clash_context = {"clash_group": "XFLTD", "clash_node": "Node A"}

    class SecretStore:
        def public_settings(self):
            return {"clash_endpoint": "http://127.0.0.1:9097"}

        def clash_secret(self):
            return "secret"

    class Controller:
        def __init__(self, endpoint, secret):
            assert (endpoint, secret) == ("http://127.0.0.1:9097", "secret")

        def get_proxy_detail_v21(self, node):
            assert node == "Node A"
            return {"type": "AnyTLS"}

    manager.secret_store = SecretStore()
    monkeypatch.setattr("app.dashboard.ClashController", Controller)

    class Collector:
        def collect(self, proxy_url):
            assert proxy_url == "http://127.0.0.1:7897"
            return {
                "ok": True,
                "full_ip": "203.0.113.10",
                "family": "IPv4",
                "country": "HK",
                "consensus": 3,
                "consensus_required": 2,
                "providers_ok": 3,
                "latency_ms": 42,
                "collected_at": "2026-08-22T00:00:00+00:00",
                "observations": [{"provider": "private", "full_ip": "203.0.113.10"}],
            }

    result = manager.observe_clash_egress(
        "http://127.0.0.1:7897", collector=Collector()
    )
    assert result == {
        "ok": True,
        "error_code": None,
        "family": "IPv4",
        "masked_ip": "203.0.xxx.xxx",
        "country": "HK",
        "consensus": 3,
        "consensus_required": 2,
        "providers_ok": 3,
        "latency_ms": 42,
        "collected_at": "2026-08-22T00:00:00+00:00",
        "group": "XFLTD",
        "node": "Node A",
        "recorded": True,
    }
    assert "full_ip" not in str(result)
    assert manager.ip_database.latest_ip_for_node("Node A")["full_ip"] == "203.0.113.10"
    assert manager.ip_identity_service.dashboard_rows()[0]["type"] == "AnyTLS"


def test_dashboard_smart_rotation_updates_context_and_checks_chrome(monkeypatch):
    manager = manager_with_database()

    class Rotator:
        def __init__(self, *_args, **_kwargs):
            pass

        def rotate(self, group, max_attempts=None):
            assert (group, max_attempts) == ("Proxy", 3)
            return {
                "ok": True,
                "node": "Node B",
                "new_ip": {"full_ip": "198.51.100.20"},
                "attempts": [{"attempt_id": 1}],
            }

    class ChromeVerifier:
        def verify(self, endpoint, expected):
            assert endpoint == "http://127.0.0.1:9222"
            assert expected == "198.51.100.20"
            return {"ok": True, "matches_expected": True}

    monkeypatch.setattr("app.dashboard.ClashIpRotator", Rotator)
    monkeypatch.setattr("app.dashboard.ChromeIpVerifier", ChromeVerifier)
    result = manager.rotate_clash(
        "http://127.0.0.1:9097", "secret", "http://127.0.0.1:7897",
        "Proxy", "http://127.0.0.1:9222", 3,
    )
    assert result["chrome_verification"]["matches_expected"] is True
    assert "full_ip" not in result["new_ip"]
    assert result["new_ip"]["masked_ip"] == "198.51.xxx.xxx"
    assert manager.current_clash_context() == {
        "clash_group": "Proxy", "clash_node": "Node B"
    }
