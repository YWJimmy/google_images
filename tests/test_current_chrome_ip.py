import pytest

from app.clash_controller import ClashControllerError
from app.current_chrome_ip import CurrentChromeIpRotator


class FakeController:
    def __init__(self):
        self.current = "Original"
        self.switches = []
        self.connection_resets = 0

    def selectors(self):
        return [
            {
                "group": "Proxy",
                "current": self.current,
                "choices": ["Original", "A", "B", "DIRECT"],
            }
        ]

    def probe_group_nodes(self, group, *, timeout_ms, max_nodes):
        assert (group, timeout_ms, max_nodes) == ("Proxy", 3000, 500)
        return {
            "nodes": [
                {"name": "A", "usable": True, "delay_ms": 50},
                {"name": "B", "usable": True, "delay_ms": 80},
                {"name": "Nested", "usable": True, "delay_ms": 20},
            ]
        }

    def switch(self, group, node):
        assert group == "Proxy"
        self.current = node
        self.switches.append((group, node))
        return {"ok": True, "current": node}

    def close_connections(self):
        self.connection_resets += 1
        return {"ok": True}


def sample(masked_ip, fingerprint, latency_ms=10):
    return {
        "ok": True,
        "masked_ip": masked_ip,
        "ip_fingerprint": fingerprint,
        "latency_ms": latency_ms,
    }


def test_rotate_keeps_first_node_that_changes_public_ip():
    controller = FakeController()
    samples = iter(
        [
            sample("203.0.113.xxx", "old"),
            sample("203.0.113.xxx", "old"),
            sample("198.51.100.xxx", "new"),
        ]
    )
    rotator = CurrentChromeIpRotator(
        controller,
        "http://127.0.0.1:7897",
        egress_check=lambda _proxy: next(samples),
        sleep=lambda _seconds: None,
    )
    result = rotator.rotate("Proxy")
    assert result["ok"] is True
    assert result["previous_node"] == "Original"
    assert result["current_node"] == "B"
    assert controller.switches == [("Proxy", "A"), ("Proxy", "B")]
    assert controller.connection_resets == 2
    assert result["current_masked_ip"] == "198.51.100.xxx"


def test_rotate_restores_original_when_no_candidate_changes_ip():
    controller = FakeController()
    samples = iter(
        [
            sample("203.0.113.xxx", "same"),
            sample("203.0.113.xxx", "same"),
            sample("203.0.113.xxx", "same"),
        ]
    )
    rotator = CurrentChromeIpRotator(
        controller,
        "http://127.0.0.1:7897",
        egress_check=lambda _proxy: next(samples),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ClashControllerError):
        rotator.rotate("Proxy")
    assert controller.switches == [
        ("Proxy", "A"),
        ("Proxy", "B"),
        ("Proxy", "Original"),
    ]
    assert controller.connection_resets == 3
    assert controller.current == "Original"


def test_nested_non_direct_choice_is_not_used():
    controller = FakeController()
    controller.probe_group_nodes = lambda *_args, **_kwargs: {
        "nodes": [
            {"name": "Nested", "usable": True, "delay_ms": 1},
            {"name": "A", "usable": True, "delay_ms": 2},
        ]
    }
    samples = iter([sample("203.0.113.xxx", "old"), sample("198.51.100.xxx", "new")])
    rotator = CurrentChromeIpRotator(
        controller,
        "http://127.0.0.1:7897",
        egress_check=lambda _proxy: next(samples),
        sleep=lambda _seconds: None,
    )
    result = rotator.rotate("Proxy")
    assert result["current_node"] == "A"
    assert controller.switches == [("Proxy", "A")]
