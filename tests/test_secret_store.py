import json
from pathlib import Path

from app.secret_store import DashboardSecretStore


class MemorySecretStore(DashboardSecretStore):
    def __init__(self):
        self.path = Path("private/dashboard_secrets.json").resolve()
        self.value = {}
        self.serialized = ""

    def _read(self) -> dict:
        return dict(self.value)

    def save_clash(self, secret: str, endpoint: str, proxy_url: str) -> None:
        from app.secret_store import dpapi_protect

        self.value = {
            "version": 1,
            "protection": "windows-dpapi-current-user",
            "clash_secret_dpapi": dpapi_protect(secret),
            "clash_endpoint": endpoint,
            "clash_proxy_url": proxy_url,
        }
        self.serialized = json.dumps(self.value, ensure_ascii=False, indent=2)


def test_secret_store_never_serializes_plaintext(monkeypatch):
    monkeypatch.setattr("app.secret_store.dpapi_protect", lambda value: "encrypted:" + value[::-1])
    monkeypatch.setattr(
        "app.secret_store.dpapi_unprotect", lambda value: value.removeprefix("encrypted:")[::-1]
    )
    store = MemorySecretStore()

    store.save_clash("very-secret", "http://127.0.0.1:9097", "http://127.0.0.1:7897")

    assert "very-secret" not in store.serialized
    assert store.clash_secret() == "very-secret"
    assert store.public_settings()["clash_secret_saved"] is True
    assert json.loads(store.serialized)["protection"] == "windows-dpapi-current-user"


def test_provided_secret_does_not_require_local_storage():
    store = MemorySecretStore()
    assert store.clash_secret("one-time-secret") == "one-time-secret"


def test_public_settings_never_returns_secret(monkeypatch):
    monkeypatch.setattr("app.secret_store.dpapi_protect", lambda _value: "encrypted-value")
    store = MemorySecretStore()
    store.save_clash("private-value", "http://127.0.0.1:9097", "http://127.0.0.1:7897")

    settings = store.public_settings()
    assert settings["clash_secret_saved"] is True
    assert "private-value" not in json.dumps(settings, ensure_ascii=False)
    assert "encrypted-value" not in json.dumps(settings, ensure_ascii=False)
