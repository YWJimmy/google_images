from app.ip_decision import IpDecisionEngine


class Scores:
    def __init__(self, values):
        self.values = values
        self.feedback = []

    def get_score(self, node):
        return self.values.get(node, {
            "score": 0, "success": 0, "fail": 0, "challenge": 0
        })

    def record_success(self, node, latency):
        self.feedback.append((node, "success", latency))

    def record_fail(self, node):
        self.feedback.append((node, "fail", None))


class History:
    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.same = []

    def get_node_status(self, node):
        return self.statuses.get(node)

    def mark_same_egress(self, ip, node=None, group=None):
        self.same.append((ip, node, group))


class Identities:
    def __init__(self, values):
        self.values = values
        self.database = None

    def for_node(self, node, reveal_full_ip=False):
        value = self.values.get(node)
        return dict(value) if value else None


def node(name):
    return {"clash_name": name, "type": "VLESS"}


def score(value, success=1, fail=0, challenge=0):
    return {"score": value, "success": success, "fail": fail, "challenge": challenge}


def test_decision_prefers_healthier_ip_over_node_score():
    engine = IpDecisionEngine(
        History(),
        Scores({"A": score(90), "B": score(70)}),
        Identities({
            "A": {"full_ip": "203.0.113.1", "ip_score": 10, "status": "ACTIVE",
                  "cluster_id": "a", "stability": {"ip_change_rate": 0}},
            "B": {"full_ip": "198.51.100.2", "ip_score": 95, "status": "ACTIVE",
                  "cluster_id": "b", "stability": {"ip_change_rate": 1}},
        }),
    )
    decision = engine.choose([node("A"), node("B")])
    assert decision["selected"]["node"]["clash_name"] == "B"
    assert decision["strategy"] == "ip_driven_v3.9"


def test_decision_filters_cooling_ip_and_duplicate_cluster():
    engine = IpDecisionEngine(
        History(), Scores({"A": score(90), "B": score(80), "C": score(70)}),
        Identities({
            "A": {"full_ip": "1.1.1.1", "ip_score": 100, "status": "COOLING",
                  "cluster_id": "x", "stability": {}},
            "B": {"full_ip": "2.2.2.2", "ip_score": 80, "status": "ACTIVE",
                  "cluster_id": "y", "stability": {}},
            "C": {"full_ip": "2.2.2.2", "ip_score": 75, "status": "ACTIVE",
                  "cluster_id": "y", "stability": {}},
        }),
    )
    decision = engine.choose([node("A"), node("B"), node("C")])
    assert decision["selected"]["node"]["clash_name"] == "B"
    assert {item["reason"] for item in decision["skipped"]} == {
        "ip_cooling", "duplicate_ip_cluster"
    }


def test_decision_supports_attempt_exclusions_and_feedback():
    scores = Scores({"A": score(90), "B": score(80)})
    history = History()
    engine = IpDecisionEngine(history, scores)
    decision = engine.choose([node("A"), node("B")], exclude_nodes={"A"})
    assert decision["selected"]["node"]["clash_name"] == "B"
    engine.feedback("A", "same_egress", full_ip="203.0.113.1", group="Proxy")
    assert scores.feedback == [("A", "fail", None)]
    assert history.same == [("203.0.113.1", "A", "Proxy")]
