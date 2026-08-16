from app.clash_controller import ClashController


class FakeController(ClashController):
    def __init__(self):
        super().__init__("http://127.0.0.1:9097", "test-secret")
        self.requests = []

    def _request(self, path, method="GET", payload=None, request_timeout=3):
        self.requests.append((path, method, payload))
        return {}


def test_close_connections_uses_mihomo_delete_connections_api():
    controller = FakeController()
    assert controller.close_connections() == {"ok": True}
    assert controller.requests == [("/connections", "DELETE", None)]
