import pytest

from app.clash_controller import ClashController, mask_ip, validate_local_http_url


class FakeController(ClashController):
    def __init__(self):
        super().__init__("http://127.0.0.1:9097", "test-secret")
        self.puts = []

    def _request(self, path, method="GET", payload=None):
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
