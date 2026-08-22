import threading

from app.ip_rotator import ClashIpRotator, RotationState


class Controller:
    def __init__(self):
        self.current = "Original"
        self.switches = []
        self.fail = set()

    def get_real_nodes_v21(self, group):
        assert group == "Proxy"
        return [
            {"clash_name": "A", "type": "VLESS"},
            {"clash_name": "B", "type": "VLESS"},
        ]

    def get_current_node_v21(self, _group):
        return self.current

    def switch_and_verify_v21(self, group, node, wait):
        self.switches.append((group, node, wait))
        if node in self.fail:
            return {"ok": False, "requested": node, "current": self.current}
        self.current = node
        return {"ok": True, "requested": node, "current": node}


class Detector:
    def find_active_group(self):
        return {"group": "Proxy"}


class Verifier:
    def __init__(self, values):
        self.values = iter(values)

    def snapshot(self, _proxy):
        return next(self.values)

    def verify_change(self, _proxy, old):
        value = next(self.values)
        return {**value, "changed": value.get("full_ip") != old}


class Engine:
    def __init__(self):
        self.feedback_values = []

    def choose(self, nodes, *, exclude_nodes=None, exclude_ips=None):
        selected = next(
            (node for node in nodes if node["clash_name"] not in (exclude_nodes or set())),
            None,
        )
        return {"selected": {"node": selected} if selected else None,
                "usable": [], "skipped": []}

    def feedback(self, node, result, **details):
        self.feedback_values.append((node, result, details.get("full_ip")))


def rotator(verifier, engine=None):
    controller = Controller()
    value = ClashIpRotator(
        "http://127.0.0.1:9097", "secret", "http://127.0.0.1:7897",
        decision_engine=engine, verifier=verifier, controller=controller,
    )
    value.detector = Detector()
    return value, controller


def test_same_egress_feedback_falls_back_to_next_decision():
    engine = Engine()
    value, controller = rotator(Verifier([
        {"ok": True, "full_ip": "203.0.113.1"},
        {"ok": True, "full_ip": "203.0.113.1"},
        {"ok": True, "full_ip": "198.51.100.2", "latency_ms": 20},
    ]), engine)
    result = value.rotate("Proxy", wait_after_switch=0)
    assert result["ok"] is True
    assert result["node"] == "B"
    assert [item["result"] for item in result["attempts"]] == ["cooling", "success"]
    assert [item[:2] for item in engine.feedback_values] == [
        ("A", "same_egress"), ("B", "success")
    ]
    assert [item[1] for item in controller.switches] == ["A", "B"]


def test_switch_failure_falls_back_and_records_attempt_ids():
    engine = Engine()
    value, controller = rotator(Verifier([
        {"ok": True, "full_ip": "203.0.113.1"},
        {"ok": True, "full_ip": "198.51.100.2"},
    ]), engine)
    controller.fail.add("A")
    result = value.rotate("Proxy", wait_after_switch=0)
    assert result["ok"] is True
    assert [item["attempt_id"] for item in result["attempts"]] == [1, 2]
    assert result["attempts"][0]["reason"] == "switch_failed"


def test_decision_mismatch_stops_without_fallback():
    class MismatchController(Controller):
        def switch_and_verify_v21(self, group, node, wait):
            return {"ok": True, "requested": "Other", "current": "Other"}

    value, _ = rotator(Verifier([{"ok": True, "full_ip": "203.0.113.1"}]), Engine())
    value.controller = MismatchController()
    result = value.rotate("Proxy", wait_after_switch=0)
    assert result["ok"] is False
    assert result["error_code"] == "DECISION_MISMATCH"


def test_concurrent_rotation_is_rejected():
    value, _ = rotator(Verifier([{"ok": True, "full_ip": "203.0.113.1"}]))
    assert value._rotation_lock.acquire(blocking=False)
    try:
        result = value.rotate("Proxy", wait_after_switch=0)
    finally:
        value._rotation_lock.release()
    assert result["error_code"] == "ROTATION_BUSY"
    assert result["busy"] is True


def test_all_failures_restore_original_node():
    engine = Engine()
    value, controller = rotator(Verifier([
        {"ok": True, "full_ip": "203.0.113.1"},
        {"ok": True, "full_ip": "203.0.113.1"},
        {"ok": True, "full_ip": "203.0.113.1"},
    ]), engine)
    result = value.rotate("Proxy", wait_after_switch=0)
    assert result["ok"] is False
    assert result["state"] == RotationState.FAILED.value
    assert result["original_restored"] is True
    assert controller.current == "Original"


def test_baseline_and_same_egress_are_both_bound_to_nodes():
    class Identities:
        def __init__(self):
            self.observations = []
            self.database = None

        def observe(self, node, full_ip, *, country=None, node_type=None):
            self.observations.append((node, full_ip, country, node_type))

    engine = Engine()
    value, _controller = rotator(Verifier([
        {"ok": True, "full_ip": "203.0.113.1", "country": "HK"},
        {"ok": True, "full_ip": "203.0.113.1", "country": "HK"},
    ]), engine)
    identities = Identities()
    value.identity_service = identities
    result = value.rotate("Proxy", wait_after_switch=0, max_attempts=1)
    assert result["ok"] is False
    assert identities.observations == [
        ("Original", "203.0.113.1", "HK", None),
        ("A", "203.0.113.1", "HK", "VLESS"),
    ]
