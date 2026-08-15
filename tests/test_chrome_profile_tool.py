from pathlib import Path

import pytest

from app.chrome_profile_tool import (
    DedicatedChromeProfiles,
    validate_port,
    validate_profile_name,
    validate_start_url,
)


def test_profile_name_rejects_paths_and_windows_reserved_names():
    assert validate_profile_name("research_01") == "research_01"
    with pytest.raises(ValueError):
        validate_profile_name("../daily")
    with pytest.raises(ValueError):
        validate_profile_name("CON")


def test_start_url_and_port_validation():
    assert validate_start_url("https://images.google.com/ncr") == "https://images.google.com/ncr"
    assert validate_port("9224") == 9224
    with pytest.raises(ValueError):
        validate_start_url("file:///private/cookies")
    with pytest.raises(ValueError):
        validate_port(80)


def memory_backed_manager(monkeypatch):
    stored = {}
    monkeypatch.setattr(
        "app.chrome_profile_tool._read_json_list",
        lambda path: [dict(item) for item in stored.get(str(path), [])],
    )
    monkeypatch.setattr(
        "app.chrome_profile_tool._write_json_private",
        lambda path, value: stored.__setitem__(str(path), [dict(item) for item in value]),
    )
    monkeypatch.setattr(Path, "mkdir", lambda *_args, **_kwargs: None)
    root = Path.cwd().resolve()
    return DedicatedChromeProfiles(root), stored


def test_create_uses_private_registry_and_profile_root(monkeypatch):
    monkeypatch.setattr("app.chrome_profile_tool.port_available", lambda _port: True)
    manager, stored = memory_backed_manager(monkeypatch)

    entry = manager.create("manual_01", 9224)

    assert entry == {
        "name": "manual_01",
        "port": 9224,
        "profile_dir": str(Path("profile") / "dedicated" / "manual_01"),
    }
    manifest = stored[str(manager.manifest_path)]
    dashboard = stored[str(manager.dashboard_registry_path)]
    assert manifest == [entry]
    assert dashboard == [
        {
            "id": "chrome-9224",
            "label": "专用 Chrome manual_01",
            "endpoint": "http://127.0.0.1:9224",
        }
    ]


def test_create_rejects_duplicate_name_or_port(monkeypatch):
    monkeypatch.setattr("app.chrome_profile_tool.port_available", lambda _port: True)
    manager, _stored = memory_backed_manager(monkeypatch)
    manager.create("one", 9224)
    with pytest.raises(ValueError):
        manager.create("one", 9225)
    with pytest.raises(ValueError):
        manager.create("two", 9224)
