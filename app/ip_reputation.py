"""IP reputation and cooling policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .database import IpIntelligenceDatabase


class IpReputationService:
    def __init__(self, database: IpIntelligenceDatabase,
                 challenge_cooling_minutes: int = 30):
        self.database = database
        self.challenge_cooling_minutes = challenge_cooling_minutes

    @staticmethod
    def score(identity: dict[str, Any] | None) -> float:
        if not identity:
            return 50.0
        success = int(identity.get("success") or 0)
        failure = int(identity.get("failure") or 0)
        challenge = int(identity.get("challenge") or 0)
        total = success + failure + challenge
        success_rate = success / total if total else 0.5
        age_bonus = 0.0
        first_seen = identity.get("first_seen")
        if first_seen:
            try:
                age_hours = max(
                    0.0,
                    (datetime.now(timezone.utc) - datetime.fromisoformat(str(first_seen))).total_seconds()
                    / 3600,
                )
                age_bonus = min(age_hours / (24 * 7) * 5, 5)
            except ValueError:
                pass
        value = 50 + success_rate * 45 + age_bonus - failure * 3 - challenge * 12
        if identity.get("status") == "COOLING":
            cooling_until = identity.get("cooling_until")
            cooling_active = True
            if cooling_until:
                try:
                    cooling_active = (
                        datetime.fromisoformat(str(cooling_until))
                        > datetime.now(timezone.utc)
                    )
                except ValueError:
                    pass
            if cooling_active:
                value -= 40
        elif identity.get("status") == "BLOCKED":
            value = 0
        return round(max(0.0, min(100.0, value)), 2)

    def effective_state(self, identity: dict[str, Any] | None) -> str:
        if not identity:
            return "ACTIVE"
        state = str(identity.get("status") or "ACTIVE").upper()
        until = identity.get("cooling_until")
        if state == "COOLING" and until:
            try:
                if datetime.fromisoformat(str(until)) <= datetime.now(timezone.utc):
                    self.database.set_ip_state(str(identity["full_ip"]), "ACTIVE")
                    return "ACTIVE"
            except ValueError:
                return "COOLING"
        return state

    def mark_challenge(self, full_ip: str, *, node: str | None = None) -> str:
        return self.cool(full_ip, "challenge", node=node)

    def mark_same_egress(self, full_ip: str, *, node: str | None = None) -> str:
        return self.cool(full_ip, "same_egress", node=node)

    def cool(self, full_ip: str, event_type: str, *, node: str | None = None,
             minutes: int | None = None) -> str:
        until = datetime.now(timezone.utc) + timedelta(
            minutes=self.challenge_cooling_minutes if minutes is None else minutes
        )
        self.database.record_event(event_type, full_ip=full_ip, node=node)
        self.database.set_ip_state(full_ip, "COOLING", until.isoformat())
        return until.isoformat()

    def block(self, full_ip: str, *, node: str | None = None,
              reason: str = "manual") -> None:
        self.database.record_event("blocked", full_ip=full_ip, node=node,
                                   details={"reason": reason})
        self.database.set_ip_state(full_ip, "BLOCKED")
