from pathlib import Path

import pytest

from app.network_rotation_test import NetworkRotationTester, private_output_path


class FakeController:
    def __init__(self):
        self.switches = []

    def selectors(self):
        return [{"group": "Proxy", "current": "Original", "choices": ["A", "B", "Original"]}]

    def probe_group_nodes(self, group, *, timeout_ms, max_nodes):
        assert (group, timeout_ms, max_nodes) == ("Proxy", 3000, 500)
        return {
            "nodes": [
                {"name": "A", "usable": True, "delay_ms": 80},
                {"name": "B", "usable": True, "delay_ms": 120},
                {"name": "Nested", "usable": True, "delay_ms": 60},
            ]
        }

    def switch(self, group, node):
        self.switches.append((group, node))
        return {"ok": True}


def test_rotation_samples_direct_nodes_and_restores_original():
    controller = FakeController()
    samples = iter(
        [
            {"masked_ip": "203.0.113.xxx", "latency_ms": 100},
            {"masked_ip": "203.0.113.xxx", "latency_ms": 120},
            {"masked_ip": "198.51.100.xxx", "latency_ms": 200},
            {"masked_ip": "198.51.100.xxx", "latency_ms": 220},
        ]
    )
    tester = NetworkRotationTester(
        controller,
        "http://127.0.0.1:7897",
        egress_check=lambda _proxy: next(samples),
        sleep=lambda _seconds: None,
    )
    result = tester.run("Proxy", node_limit=2, samples_per_node=2)
    assert result["google_accessed"] is False
    assert result["tested_nodes"] == 2
    assert result["usable_nodes"] == 2
    assert result["original_restored"] is True
    assert controller.switches == [
        ("Proxy", "A"),
        ("Proxy", "B"),
        ("Proxy", "Original"),
    ]
    assert result["results"][0]["median_ms"] == 110


def test_rotation_restores_original_after_unexpected_failure():
    controller = FakeController()
    tester = NetworkRotationTester(
        controller,
        "http://127.0.0.1:7897",
        egress_check=lambda _proxy: (_ for _ in ()).throw(RuntimeError("offline")),
        sleep=lambda _seconds: None,
    )
    result = tester.run("Proxy", node_limit=1, samples_per_node=1)
    assert result["usable_nodes"] == 0
    assert controller.switches[-1] == ("Proxy", "Original")


def test_output_is_restricted_to_private_directory():
    root = Path.cwd()
    assert private_output_path(root, "private/result.json").parent == (root / "private")
    with pytest.raises(ValueError):
        private_output_path(root, "logs/result.json")
