"""Rotation Pipeline v3.9: select, switch, verify and feed back."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import inspect
import threading
import uuid
from typing import Any

from .clash_route_detector import ClashRouteDetector
from .full_ip import mask_full_ip
from .ip_verifier import FullIpVerifier


class RotationState(str, Enum):
    ROTATING = "ROTATING"
    VERIFYING = "VERIFYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    COOLING = "COOLING"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClashIpRotator:
    def __init__(self, endpoint, secret, proxy_url, decision_engine=None, *,
                 verifier=None, identity_service=None, database=None,
                 controller=None):
        if controller is None:
            from .clash_controller import ClashController
            controller = ClashController(endpoint, secret)
        self.controller = controller
        self.proxy_url = proxy_url
        self.detector = ClashRouteDetector(controller, proxy_url)
        self.decision_engine = decision_engine
        self.verifier = verifier or FullIpVerifier()
        self.identity_service = (
            identity_service
            or getattr(decision_engine, "identity_service", None)
        )
        self.database = (
            database
            or getattr(self.identity_service, "database", None)
        )
        self._rotation_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._state = RotationState.FAILED
        self._active_rotation_id: str | None = None

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "state": self._state.value,
                "rotation_id": self._active_rotation_id,
                "busy": self._rotation_lock.locked(),
            }

    def _set_state(self, state: RotationState, rotation_id: str | None) -> None:
        with self._state_lock:
            self._state = state
            self._active_rotation_id = rotation_id

    def current(self):
        group = self.detector.find_active_group()
        sample = self.verifier.snapshot(self.proxy_url)
        public = dict(sample)
        if public.get("full_ip"):
            public["masked_ip"] = mask_full_ip(public["full_ip"])
        return {"ok": sample.get("ok"), "group": group, "ip": public}

    @staticmethod
    def _node_name(node: Any) -> str | None:
        if isinstance(node, dict):
            value = node.get("clash_name")
        else:
            value = node
        return str(value) if value else None

    def _choose(self, engine, nodes, attempted_nodes, attempted_ips):
        if engine is None:
            selected = next(
                (node for node in nodes
                 if self._node_name(node) not in attempted_nodes),
                None,
            )
            return {
                "selected": {"node": selected} if selected else None,
                "usable": [], "skipped": [], "strategy": "discovery_order",
            }
        parameters = inspect.signature(engine.choose).parameters
        if "exclude_nodes" in parameters:
            return engine.choose(
                nodes,
                exclude_nodes=attempted_nodes,
                exclude_ips=attempted_ips,
            )
        remaining = [
            node for node in nodes if self._node_name(node) not in attempted_nodes
        ]
        return engine.choose(remaining)

    @staticmethod
    def _feedback(engine, node: str, result: str, *, full_ip=None,
                  group=None, latency_ms=None) -> str | None:
        try:
            if engine is None:
                return None
            handler = getattr(engine, "feedback", None)
            if callable(handler):
                handler(node, result, full_ip=full_ip, group=group,
                        latency_ms=latency_ms)
                return None
            score_store = getattr(engine, "score_store", None)
            if score_store is None:
                return None
            if result == "success":
                score_store.record_success(node, latency_ms)
            else:
                score_store.record_fail(node)
            return None
        except Exception as exc:
            return type(exc).__name__

    def _record_attempt(self, rotation_id: str, attempt: dict[str, Any]) -> None:
        if self.database is None:
            return
        try:
            self.database.record_rotation(
                rotation_id,
                int(attempt["attempt_id"]),
                node=attempt.get("node"),
                decision_node=attempt.get("decision_selected"),
                full_ip=attempt.get("new_full_ip"),
                result=str(attempt.get("result") or "failed"),
                state=str(attempt.get("state") or RotationState.FAILED.value),
                reason=attempt.get("reason"),
                details={
                    "started_at": attempt.get("started_at"),
                    "finished_at": attempt.get("finished_at"),
                },
            )
        except Exception as exc:
            attempt["persistence_error"] = type(exc).__name__

    def _finish_attempt(self, rotation_id: str, attempt: dict[str, Any], *,
                        result: str, state: RotationState,
                        reason: str | None = None, full_ip: str | None = None) -> None:
        attempt.update({
            "result": result,
            "state": state.value,
            "reason": reason,
            "new_full_ip": full_ip,
            "new_masked_ip": mask_full_ip(full_ip) if full_ip else None,
            "finished_at": _now(),
        })
        self._record_attempt(rotation_id, attempt)

    def rotate(self, group=None, wait_after_switch=3, decision_engine=None,
               max_attempts: int | None = None):
        if not self._rotation_lock.acquire(blocking=False):
            return {
                "ok": False,
                "error_code": "ROTATION_BUSY",
                **self.status(),
            }
        rotation_id = uuid.uuid4().hex
        self._set_state(RotationState.ROTATING, rotation_id)
        try:
            return self._rotate_locked(
                rotation_id, group, wait_after_switch,
                decision_engine or self.decision_engine, max_attempts,
            )
        except Exception as exc:
            self._set_state(RotationState.FAILED, None)
            return {
                "ok": False,
                "error_code": "ROTATION_INTERNAL_ERROR",
                "rotation_id": rotation_id,
                "state": RotationState.FAILED.value,
                "reason": type(exc).__name__,
            }
        finally:
            self._rotation_lock.release()

    def _rotate_locked(self, rotation_id, group, wait_after_switch, engine,
                       max_attempts):
        attempts: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        attempted_nodes: set[str] = set()
        attempted_ips: set[str] = set()

        if group is None:
            detected = self.detector.find_active_group()
            if not detected:
                self._set_state(RotationState.FAILED, None)
                return {
                    "ok": False, "error_code": "NO_ACTIVE_GROUP",
                    "rotation_id": rotation_id, "state": RotationState.FAILED.value,
                }
            group = detected["group"]

        old = self.verifier.snapshot(self.proxy_url)
        if not old.get("ok") or not old.get("full_ip"):
            self._set_state(RotationState.FAILED, None)
            return {
                "ok": False, "error_code": old.get("error_code", "FULL_IP_UNAVAILABLE"),
                "rotation_id": rotation_id, "group": group,
                "state": RotationState.FAILED.value, "old_ip": old,
            }
        old_full_ip = str(old["full_ip"])
        attempted_ips.add(old_full_ip)
        nodes = self.controller.get_real_nodes_v21(group)
        discovered_names = {self._node_name(node) for node in nodes}
        limit = min(len(nodes), max_attempts or len(nodes))
        try:
            # 恢复时必须保存 Selector 的直接 now；若原值是“自动选择”，只保存其
            # 当前叶子会把自动策略永久改成固定节点。旧测试替身没有新方法时兼容回退。
            current_selection = getattr(
                self.controller, "get_current_selection_v21", None
            )
            if current_selection is None:
                current_selection = self.controller.get_current_node_v21
            original_node = current_selection(group)
        except Exception:
            original_node = None

        while len(attempts) < limit:
            self._set_state(RotationState.ROTATING, rotation_id)
            decision = self._choose(engine, nodes, attempted_nodes, attempted_ips)
            decisions.append(decision)
            selected = decision.get("selected") if isinstance(decision, dict) else None
            selected_node = selected.get("node") if isinstance(selected, dict) else None
            name = self._node_name(selected_node)
            if not name:
                break
            if name not in discovered_names:
                self._set_state(RotationState.FAILED, None)
                return {
                    "ok": False, "error_code": "INVALID_DECISION_RESULT",
                    "rotation_id": rotation_id, "group": group,
                    "state": RotationState.FAILED.value,
                    "decision": decision, "attempts": attempts,
                }
            attempted_nodes.add(name)
            attempt = {
                "attempt_id": len(attempts) + 1,
                "node": name,
                "decision_selected": name,
                "switch_target": name,
                "started_at": _now(),
                "state": RotationState.ROTATING.value,
            }
            attempts.append(attempt)
            if attempt["decision_selected"] != attempt["switch_target"]:
                self._finish_attempt(
                    rotation_id, attempt, result="failed",
                    state=RotationState.FAILED, reason="DECISION_MISMATCH",
                )
                self._set_state(RotationState.FAILED, None)
                return {
                    "ok": False, "error_code": "DECISION_MISMATCH",
                    "rotation_id": rotation_id, "group": group,
                    "state": RotationState.FAILED.value, "attempts": attempts,
                }

            try:
                switched = self.controller.switch_and_verify_v21(
                    group, name, wait=wait_after_switch
                )
                if not switched.get("ok"):
                    reason = "switch_failed"
                elif (
                    switched.get("requested", name) != name
                    or switched.get("current", name) != name
                ):
                    reason = "DECISION_MISMATCH"
                else:
                    reason = None
                if reason:
                    feedback_error = self._feedback(engine, name, reason, group=group)
                    if feedback_error:
                        attempt["feedback_error"] = feedback_error
                    self._finish_attempt(
                        rotation_id, attempt, result="failed",
                        state=RotationState.FAILED, reason=reason,
                    )
                    if reason == "DECISION_MISMATCH":
                        self._set_state(RotationState.FAILED, None)
                        return {
                            "ok": False, "error_code": reason,
                            "rotation_id": rotation_id, "group": group,
                            "state": RotationState.FAILED.value, "attempts": attempts,
                        }
                    continue

                self._set_state(RotationState.VERIFYING, rotation_id)
                verified = self.verifier.verify_change(self.proxy_url, old_full_ip)
                new_full_ip = verified.get("full_ip")
                if not verified.get("ok"):
                    reason = verified.get("error_code", "verification_failed")
                    feedback_error = self._feedback(
                        engine, name, reason, full_ip=new_full_ip, group=group
                    )
                    if feedback_error:
                        attempt["feedback_error"] = feedback_error
                    self._finish_attempt(
                        rotation_id, attempt, result="failed",
                        state=RotationState.FAILED, reason=reason,
                        full_ip=new_full_ip,
                    )
                    continue
                if not verified.get("changed"):
                    attempted_ips.add(str(new_full_ip))
                    feedback_error = self._feedback(
                        engine, name, "same_egress", full_ip=new_full_ip, group=group
                    )
                    if feedback_error:
                        attempt["feedback_error"] = feedback_error
                    self._finish_attempt(
                        rotation_id, attempt, result="cooling",
                        state=RotationState.COOLING, reason="same_egress",
                        full_ip=new_full_ip,
                    )
                    self._set_state(RotationState.COOLING, rotation_id)
                    continue

                if self.identity_service is not None:
                    try:
                        self.identity_service.observe(
                            name, str(new_full_ip), country=verified.get("country"),
                            node_type=selected_node.get("type") if isinstance(selected_node, dict) else None,
                        )
                    except Exception as exc:
                        attempt["identity_error"] = type(exc).__name__
                feedback_error = self._feedback(
                    engine, name, "success", full_ip=new_full_ip,
                    group=group, latency_ms=verified.get("latency_ms"),
                )
                if feedback_error:
                    attempt["feedback_error"] = feedback_error
                self._finish_attempt(
                    rotation_id, attempt, result="success",
                    state=RotationState.SUCCESS, full_ip=str(new_full_ip),
                )
                self._set_state(RotationState.SUCCESS, None)
                return {
                    "ok": True, "rotation_id": rotation_id,
                    "state": RotationState.SUCCESS.value, "group": group,
                    "node": name, "old_ip": old, "new_ip": verified,
                    "attempts": attempts, "decisions": decisions,
                }
            except Exception as exc:
                reason = type(exc).__name__
                feedback_error = self._feedback(engine, name, "failure", group=group)
                if feedback_error:
                    attempt["feedback_error"] = feedback_error
                self._finish_attempt(
                    rotation_id, attempt, result="failed",
                    state=RotationState.FAILED, reason=reason,
                )

        restored = False
        if original_node and original_node not in {attempts[-1]["node"] if attempts else None}:
            try:
                restored = bool(self.controller.switch_and_verify_v21(
                    group, original_node, wait=wait_after_switch
                ).get("ok"))
            except Exception:
                restored = False
        self._set_state(RotationState.FAILED, None)
        return {
            "ok": False,
            "error_code": "ALL_NODE_FAILED" if attempts else "NO_USABLE_NODE",
            "rotation_id": rotation_id, "state": RotationState.FAILED.value,
            "group": group, "attempts": attempts, "decisions": decisions,
            "original_restored": restored,
        }
