import pytest

from app.clash_controller import ClashController, mask_ip, validate_local_http_url


class FakeController(ClashController):
    def __init__(self):
        super().__init__("http://127.0.0.1:9097", "test-secret")
        self.puts = []

    def _request(self, path, method="GET", payload=None, request_timeout=3):
        if path == "/version":
            return {"version": "1.2.3"}
        if path == "/proxies":
            return {
                "proxies": {
                    "GLOBAL": {"type": "Selector", "now": "Node A", "all": ["Node A", "Node B"]},
                    "DIRECT": {"type": "Direct", "now": "DIRECT", "all": []},
                }
            }
        self.puts.append((path, method, payload))
        return {}


def test_clash_endpoints_must_be_loopback_only():
    assert validate_local_http_url("http://127.0.0.1:9097/", "endpoint") == "http://127.0.0.1:9097"
    with pytest.raises(ValueError):
        validate_local_http_url("http://0.0.0.0:9097", "endpoint")
    with pytest.raises(ValueError):
        validate_local_http_url("https://127.0.0.1:9097", "endpoint")


def test_ip_masking_never_returns_full_address():
    assert mask_ip("203.0.113.42") == "203.0.113.xxx"
    assert mask_ip("2001:db8::1") == "2001:0db8:0000:0000:…"


def test_manual_selector_switch_validates_live_choices():
    controller = FakeController()
    status = controller.status()
    assert status["selectors"][0]["current"] == "Node A"
    controller.switch("GLOBAL", "Node B")
    assert controller.puts == [("/proxies/GLOBAL", "PUT", {"name": "Node B"})]
    with pytest.raises(ValueError):
        controller.switch("GLOBAL", "Unknown Node")


class ProbeController(ClashController):
    def __init__(self):
        super().__init__("http://127.0.0.1:9097", "test-secret")
        self.requests = []

    def _request(self, path, method="GET", payload=None, request_timeout=3):
        self.requests.append((path, method, request_timeout))
        if path == "/proxies":
            return {
                "proxies": {
                    "GLOBAL": {
                        "type": "Selector",
                        "now": "Auto",
                        "all": ["Auto", "Node C", "DIRECT"],
                    },
                    "Auto": {"type": "URLTest", "all": ["Node A", "Node B"]},
                    "Node A": {"type": "Vmess", "server": "203.0.113.10"},
                    "Node B": {"type": "Trojan", "server": "198.51.100.20"},
                    "Node C": {"type": "Shadowsocks", "server": "192.0.2.30"},
                    "DIRECT": {"type": "Direct", "all": []},
                }
            }
        if "/Node%20A/delay?" in path:
            return {"delay": 120}
        if "/Node%20C/delay?" in path:
            return {"delay": 50}
        raise RuntimeError("unreachable")


def test_probe_group_nodes_tests_leaf_nodes_without_switching_or_exposing_servers():
    controller = ProbeController()
    result = controller.probe_group_nodes("GLOBAL", timeout_ms=2500)
    assert result["tested"] == 3
    assert result["usable"] == 2
    assert result["unusable"] == 1
    assert result["test_url"] == "https://cp.cloudflare.com/generate_204"
    assert [item["name"] for item in result["nodes"]] == ["Node C", "Node A", "Node B"]
    assert result["nodes"][0]["delay_ms"] == 50
    assert all("server" not in item for item in result["nodes"])
    assert not any(method == "PUT" for _path, method, _timeout in controller.requests)
    delay_paths = [path for path, _method, _timeout in controller.requests if "/delay?" in path]
    assert len(delay_paths) == 3
    assert all("expected=204" in path for path in delay_paths)


def test_probe_group_nodes_rejects_unknown_groups_and_unbounded_timeouts():
    controller = ProbeController()
    with pytest.raises(ValueError):
        controller.probe_group_nodes("missing")
    with pytest.raises(ValueError):
        controller.probe_group_nodes("GLOBAL", timeout_ms=20000)
