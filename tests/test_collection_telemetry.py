import json
from pathlib import Path
import uuid

from app.collection_telemetry import (
    CollectionTelemetry,
    profile_network_snapshot,
)


def test_verification_events_are_numbered_per_run_and_keep_masked_network_state():
    path = Path("logs") / f"_pytest_collection_{uuid.uuid4().hex}.sqlite3"
    telemetry = CollectionTelemetry(path)
    try:
        run_id = telemetry.start_run(mode="test", requested_count=10)
        first = telemetry.record_human_verification(
            run_id=run_id,
            event_type="challenge",
            task_index=4,
            search_sequence_number=4,
            task_attempt_number=1,
            completed_before=3,
            actual_start_gap_ms=6000,
            configured_delay_seconds=6,
            profile_name="manual_02",
            masked_ip="203.0.113.xxx",
            proxy_status="online",
            proxy_latency_ms=245,
            egress_checked_at="2026-08-15T22:00:00",
        )
        second = telemetry.record_human_verification(
            run_id=run_id,
            event_type="consent",
            task_index=7,
            search_sequence_number=8,
            task_attempt_number=2,
            completed_before=6,
            actual_start_gap_ms=6100,
            configured_delay_seconds=6,
            profile_name="manual_02",
            masked_ip="203.0.113.xxx",
            proxy_status="verification_seen",
            proxy_latency_ms=245,
        )

        events = telemetry.recent_events()
        assert (first, second) == (1, 2)
        assert [event["verification_ordinal"] for event in events] == [2, 1]
        assert events[0]["task_index"] == 7
        assert events[0]["masked_ip"] == "203.0.113.xxx"
        assert events[0]["proxy_status"] == "verification_seen"
        assert events[0]["ip_event_status"] == "verification_seen"
        assert events[0]["proxy_latency_ms"] == 245
    finally:
        if path.exists():
            path.unlink()


def test_profile_network_snapshot_never_reads_or_returns_proxy_credentials(monkeypatch):
    payload = [
        {
            "name": "manual_02",
            "port": 9223,
            "proxy_url": "http://127.0.0.1:7898",
            "masked_ip": "198.51.100.xxx",
            "proxy_status": "online",
            "proxy_latency_ms": 180,
            "proxy_checked_at": "2026-08-15T22:00:00",
        }
    ]
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: json.dumps(payload))

    snapshot = profile_network_snapshot(Path.cwd(), "http://127.0.0.1:9223")

    assert snapshot["profile_name"] == "manual_02"
    assert snapshot["masked_ip"] == "198.51.100.xxx"
    assert snapshot["proxy_latency_ms"] == 180
    assert "proxy_url" not in snapshot
