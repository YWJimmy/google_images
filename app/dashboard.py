from __future__ import annotations
# 本模块为 Google 图片自动搜索系统的一部分。
# 整体流程：任务调度 -> 浏览器自动化 -> Google页面解析 -> 结果返回 -> 状态记录。
# 以下注释仅用于解释工程设计，不改变任何执行逻辑。


import argparse
from dataclasses import replace
from datetime import datetime
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
import uuid
import webbrowser

from .config import Config, load_config
from .chrome_profile_tool import DEFAULT_START_URL, DedicatedChromeProfiles, endpoint_online
from .collection_telemetry import (
    CollectionTelemetry,
    profile_network_snapshot,
    telemetry_path,
)
from .clash_controller import (
    ClashController,
    masked_proxy_egress,
    validate_local_http_url,
    validate_secret,
)
from .secret_store import DashboardSecretStore
from .database import IpIntelligenceDatabase
from .error_codes import describe
from .ip_decision import IpDecisionEngine
from .full_ip import FullIpCollector, mask_full_ip
from .ip_history import IpHistoryStore
from .ip_identity import IpIdentityService
from .ip_reputation import IpReputationService
from .ip_rotator import ClashIpRotator
from .ip_verifier import ChromeIpVerifier
from .node_score import NodeScoreStore
from .google_images import (
    BrowserLaunchError,
    ChallengeDetected,
    ConsentRequired,
    GoogleImagesBrowser,
    NavigationError,
    SearchParseTimeout,
    TIMEOUT_STAGE_LABELS,
    format_search_metrics,
)
from .ranking_decision import decide_source_rank
from .ranking import is_google_host
from .storage import load_tasks


HUMAN_POLL_SECONDS = 10.0
LOCAL_CDP_HOSTS = {"127.0.0.1", "localhost", "::1"}
RUN_LABELS = {
    "idle": "空闲",
    "starting": "正在连接",
    "running": "运行中",
    "waiting_for_human": "等待人工验证",
    "stopping": "正在停止",
    "stopped": "已停止",
    "complete": "已完成",
    "error": "运行错误",
}
TASK_LABELS = {
    "pending": "等待",
    "searching": "搜索中",
    "waiting_for_human": "等待人工验证",
    "found": "已命中",
    "not_found": "Top-N 未命中",
    "incomplete": "结果不完整",
    "timeout": "阶段超时跳过",
    "navigation_error": "导航错误",
    "error": "内部错误",
    "stopped": "已停止",
    "resumed": "此前已完成",
}


# 功能：validate_local_cdp_endpoint 函数，负责当前模块中的一项具体处理逻辑。
def validate_local_cdp_endpoint(endpoint: str) -> str:
    value = endpoint.strip().rstrip("/")
    parts = urlsplit(value)
# port表示网络端口，用于区分同一主机上的不同服务入口。
    if parts.scheme != "http" or parts.hostname not in LOCAL_CDP_HOSTS or not parts.port:
        raise ValueError("CDP endpoint must be a local http URL with an explicit port")
    return value


# 功能：validate_history_url 函数，负责当前模块中的一项具体处理逻辑。
def validate_history_url(url: str) -> str:
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("history URL must be an absolute http(s) URL")
    return value


# 功能：classify_cdp_page_urls 函数，负责当前模块中的一项具体处理逻辑。
def classify_cdp_page_urls(urls: list[str]) -> str:
    """Classify local CDP tab URLs without retaining query strings or page content."""
    saw_google = False
    for value in urls:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        path = parts.path.lower()
        if is_google_host(host):
            saw_google = True
# /sorry/ 是 Google 异常流量检测页面常见路径，用于识别风控状态。
        if is_google_host(host) and "/sorry/" in path:
            return "challenge"
        if host.startswith("consent.google."):
            return "consent"
    if saw_google:
        return "normal"
    return "other" if urls else "no_pages"


# 类说明：ChromeSlot 封装相关业务状态和操作。
class ChromeSlot:
# 功能：__init__ 函数，负责当前模块中的一项具体处理逻辑。
    def __init__(self, slot_id: str, label: str, endpoint: str, cfg: Config):
        self.id = slot_id
        self.label = label
        self.endpoint = validate_local_cdp_endpoint(endpoint)
        self.cfg = cfg
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.chrome_online = False
        self.last_probe_at: float | None = None
        self.run_state = "idle"
        self.page_state = "unknown"
        self.current_index: int | None = None
        self.completed_count = 0
        self.tasks: list[dict] = []
        self.next_human_poll_at: float | None = None
        self.message = ""
        self.manual_navigation_state = "idle"
        self.telemetry = CollectionTelemetry(telemetry_path(cfg.log_dir))
        self.telemetry_run_id: str | None = None
        self.telemetry_error = ""
        self.search_attempt_count = 0
        self.verification_count = 0
        self._previous_search_started: float | None = None
        self.clash_context_provider = None
        self.ip_challenge_recorder = None

    @property
# 功能：active 函数，负责当前模块中的一项具体处理逻辑。
    def active(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

# 功能：_read_cdp_page_state 函数，负责当前模块中的一项具体处理逻辑。
    def _read_cdp_page_state(self) -> str:
        with urlopen(self.endpoint + "/json", timeout=2) as response:
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
            targets = json.loads(response.read().decode("utf-8"))
        return classify_cdp_page_urls(
            [str(target.get("url", "")) for target in targets if target.get("type") == "page"]
        )

# 功能：probe 函数，负责当前模块中的一项具体处理逻辑。
    def probe(self) -> None:
        online = False
        page_state = "unknown"
        try:
            with urlopen(self.endpoint + "/json/version", timeout=2) as response:
                online = response.status == 200
            page_state = self._read_cdp_page_state()
        except Exception:
            online = False
        with self.lock:
            self.chrome_online = online
            self.last_probe_at = time.time()
            if not self.active:
                self.page_state = page_state

# 功能：生成 Dashboard展示所需状态快照。
# 包含：任务、Chrome、Clash等运行信息。

    def snapshot(self) -> dict:
        with self.lock:
            remaining = None
            if self.next_human_poll_at is not None:
                remaining = max(0, int(round(self.next_human_poll_at - time.time())))
            return {
                "id": self.id,
                "label": self.label,
                "endpoint": self.endpoint,
                "chrome_online": self.chrome_online,
                "active": self.active,
                "run_state": self.run_state,
                "run_state_label": RUN_LABELS.get(self.run_state, self.run_state),
                "page_state": self.page_state,
                "current_index": self.current_index,
                "completed_count": self.completed_count,
                "total_count": len(self.tasks),
                "next_human_poll_seconds": remaining,
                "message": self.message,
                "manual_navigation_state": self.manual_navigation_state,
                "telemetry_run_id": self.telemetry_run_id,
                "telemetry_error": self.telemetry_error,
                "search_attempt_count": self.search_attempt_count,
                "verification_count": self.verification_count,
                "tasks": [dict(task) for task in self.tasks],
            }

# 功能：启动一次新的自动搜索任务。

    def start_run(
        self, limit: int, max_results: int, post_delay: float, start_index: int = 1
    ) -> None:
        with self.lock:
            if self.active:
                raise ValueError("this Chrome window already has an active task")
            if self.manual_navigation_state == "running":
                raise ValueError("this Chrome window is adding a History entry")
            source_tasks = load_tasks(self.cfg.input_csv, limit)
            if not source_tasks:
                raise ValueError("no valid keyword tasks found")
            if not (1 <= start_index <= len(source_tasks)):
                raise ValueError("start_index must be within the selected sample range")
            self.tasks = [
                {
                    "index": index,
                    "keyword": task.keyword,
                    "target_domain": task.target_domain,
                    "status": "resumed" if index < start_index else "pending",
                    "status_label": TASK_LABELS["resumed" if index < start_index else "pending"],
                    "rank": None,
                    "resolved_count": None,
                    "elapsed_ms": None,
                    "message": "",
                    "timing": "",
                    "timeout_stage": None,
                    "retry_action": None,
                    "verification_ordinal": None,
                    "attempts": 0,
                }
                for index, task in enumerate(source_tasks, start=1)
            ]
            self.stop_event.clear()
            self.completed_count = start_index - 1
            self.current_index = None
            self.page_state = "unknown"
            self.run_state = "starting"
            self.message = ""
            self.telemetry_error = ""
            self.search_attempt_count = 0
            self.verification_count = 0
            self._previous_search_started = None
            try:
                self.telemetry_run_id = self.telemetry.start_run(
                    mode="dashboard_cdp",
                    requested_count=len(source_tasks),
                    start_index=start_index,
                    configured_delay_seconds=post_delay,
                    chrome_id=self.id,
                    session_mode="manual_cdp",
                )
            except Exception as exc:
                self.telemetry_run_id = None
                self.telemetry_error = type(exc).__name__
            self.worker = threading.Thread(
                target=self._run,
                args=(source_tasks, max_results, post_delay, start_index),
                name=f"chrome-run-{self.id}",
                daemon=True,
            )
            self.worker.start()

# 功能：停止正在运行的后台任务。

    def stop(self) -> None:
        with self.lock:
            if self.active:
                self.run_state = "stopping"
                self.stop_event.set()

# 功能：_set_task 函数，负责当前模块中的一项具体处理逻辑。
    def _set_task(self, index: int, **changes) -> None:
        with self.lock:
            task = self.tasks[index - 1]
            task.update(changes)
            status = str(task.get("status", "pending"))
            task["status_label"] = TASK_LABELS.get(status, status)

# 功能：_wait_for_human 函数，负责当前模块中的一项具体处理逻辑。
    def _wait_for_human(self, browser: GoogleImagesBrowser) -> bool:
        while not self.stop_event.is_set():
            with self.lock:
                self.next_human_poll_at = time.time() + HUMAN_POLL_SECONDS
            if self.stop_event.wait(HUMAN_POLL_SECONDS):
                return False
            try:
                page_state = self._read_cdp_page_state()
                if page_state == "normal":
                    # A page completed manually can be correct in Chrome while a
                    # long-lived Playwright-over-CDP Page retains its pre-solve URL.
                    # Reconnecting refreshes Playwright's target/frame cache without
                    # closing or navigating the user's Chrome window.
                    browser.close()
                    browser.start()
                    page_state, _ = browser.page_state()
            except Exception:
                page_state = "unknown"
            with self.lock:
                self.page_state = page_state
                self.next_human_poll_at = None
            if page_state == "normal":
                return True
        return False

# 功能：Dashboard后台线程实际执行入口。

    def _run(
        self, source_tasks, max_results: int, post_delay: float, start_index: int = 1
    ) -> None:
        browser = GoogleImagesBrowser(
            replace(self.cfg, session_mode="manual_cdp", cdp_endpoint=self.endpoint)
        )
        try:
            browser.start()
            with self.lock:
                self.run_state = "running"
                self.chrome_online = True
            for index, task in enumerate(source_tasks[start_index - 1 :], start=start_index):
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.current_index = index
                while not self.stop_event.is_set():
                    task_attempt_number = self.tasks[index - 1]["attempts"] + 1
                    self._set_task(index, status="searching", attempts=task_attempt_number)
                    search_started = time.monotonic()
                    actual_start_gap_ms = (
                        round((search_started - self._previous_search_started) * 1000)
                        if self._previous_search_started is not None
                        else None
                    )
                    self._previous_search_started = search_started
                    self.search_attempt_count += 1
                    search_sequence_number = self.search_attempt_count
                    if self.telemetry_run_id:
                        try:
                            self.telemetry.update_attempt_count(
                                self.telemetry_run_id, search_sequence_number
                            )
                        except Exception as exc:
                            self.telemetry_error = type(exc).__name__
                    started = time.perf_counter()
                    try:
                        _google_url, items, elapsed_ms = browser.search(task.keyword, max_results)
                        decision = decide_source_rank(
                            browser.last_source_observations,
                            task.target_domain,
                            max_results,
                            self.cfg.include_subdomains,
                        )
                        if decision.result_code > 0:
                            status = "found"
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                        elif decision.result_code == -1:
                            status = "not_found"
                        else:
                            status = "incomplete"
                        self._set_task(
                            index,
                            status=status,
                            rank=decision.matched_rank,
                            resolved_count=len(items),
                            elapsed_ms=elapsed_ms,
                            message=decision.message or "",
                            timing=format_search_metrics(browser.last_search_metrics),
                            retry_action=browser.last_search_metrics.get("recovery", "none"),
                        )
                        with self.lock:
                            self.page_state = "normal"
                        break
                    except (ChallengeDetected, ConsentRequired) as exc:
                        waiting_state = "challenge" if isinstance(exc, ChallengeDetected) else "consent"
                        self.verification_count += 1
                        verification_ordinal = self.verification_count
                        if self.telemetry_run_id:
                            try:
                                network = profile_network_snapshot(
                                    self.cfg.log_dir.parent, self.endpoint
                                )
                                clash_context = (
                                    self.clash_context_provider()
                                    if callable(self.clash_context_provider)
                                    else {}
                                )
                                verification_ordinal = self.telemetry.record_human_verification(
                                    run_id=self.telemetry_run_id,
                                    event_type=waiting_state,
                                    task_index=index,
                                    search_sequence_number=search_sequence_number,
                                    task_attempt_number=task_attempt_number,
                                    completed_before=self.completed_count,
                                    actual_start_gap_ms=actual_start_gap_ms,
                                    configured_delay_seconds=post_delay,
                                    chrome_id=self.id,
                                    clash_group=clash_context.get("clash_group"),
                                    clash_node=clash_context.get("clash_node"),
                                    **network,
                                )
                            except Exception as telemetry_exc:
                                self.telemetry_error = type(telemetry_exc).__name__
                        if waiting_state == "challenge" and callable(self.ip_challenge_recorder):
                            try:
                                clash_context = (
                                    self.clash_context_provider()
                                    if callable(self.clash_context_provider)
                                    else {}
                                )
                                self.ip_challenge_recorder(clash_context.get("clash_node"))
                            except Exception as reputation_exc:
                                self.telemetry_error = type(reputation_exc).__name__
                        self._set_task(
                            index,
                            status="waiting_for_human",
                            verification_ordinal=verification_ordinal,
                            message=f"本次运行第 {verification_ordinal} 次验证：{exc}",
                        )
                        with self.lock:
                            self.run_state = "waiting_for_human"
                            self.page_state = waiting_state
                            self.message = "请在对应专用 Chrome 窗口中完成人工处理"
                        if not self._wait_for_human(browser):
                            break
                        with self.lock:
                            self.run_state = "running"
                            self.message = "人工处理完成，正在重试当前关键词"
                        continue
                    except SearchParseTimeout as exc:
                        stage_label = TIMEOUT_STAGE_LABELS.get(exc.stage, exc.stage)
                        timing = format_search_metrics(exc.metrics)
                        self._set_task(
                            index,
                            status="timeout",
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            message=f"{stage_label}：{exc}",
                            timing=timing,
                            timeout_stage=exc.stage,
                            retry_action=exc.recovery,
                        )
                        break
                    except NavigationError as exc:
                        self._set_task(index, status="navigation_error", message=str(exc))
                        break
                    except Exception as exc:
                        self._set_task(index, status="error", message=type(exc).__name__)
                        break
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.completed_count = index
                if index < len(source_tasks) and self.stop_event.wait(post_delay):
                    break
            with self.lock:
                if self.stop_event.is_set():
                    self.run_state = "stopped"
                else:
                    self.run_state = "complete"
                    self.message = "任务已完成"
        except BrowserLaunchError as exc:
            with self.lock:
                self.run_state = "error"
                self.message = str(exc)
                self.chrome_online = False
        finally:
            browser.close()
            with self.lock:
                self.next_human_poll_at = None
                if self.stop_event.is_set() and self.current_index:
                    current = self.tasks[self.current_index - 1]
                    if current["status"] in {"pending", "searching", "waiting_for_human"}:
                        current["status"] = "stopped"
                        current["status_label"] = TASK_LABELS["stopped"]
                telemetry_status = self.run_state
                telemetry_completed = self.completed_count
                telemetry_attempts = self.search_attempt_count
            if self.telemetry_run_id:
                try:
                    self.telemetry.finish_run(
                        self.telemetry_run_id,
                        status=telemetry_status,
                        completed_count=telemetry_completed,
                        search_attempt_count=telemetry_attempts,
                    )
                except Exception as exc:
                    with self.lock:
                        self.telemetry_error = type(exc).__name__

# 功能：控制浏览器访问历史相关页面，用于状态展示。

    def navigate_for_history(self, url: str) -> None:
        value = validate_history_url(url)
        with self.lock:
            if self.active or self.manual_navigation_state == "running":
                raise ValueError("Chrome window is busy")
            self.manual_navigation_state = "running"
        threading.Thread(
            target=self._navigate_for_history,
            args=(value,),
            name=f"chrome-history-{self.id}",
            daemon=True,
        ).start()

# 功能：_navigate_for_history 函数，负责当前模块中的一项具体处理逻辑。
    def _navigate_for_history(self, url: str) -> None:
        browser = GoogleImagesBrowser(
            replace(self.cfg, session_mode="manual_cdp", cdp_endpoint=self.endpoint)
        )
        try:
            browser.start()
            history_page = browser.context.new_page()
            history_page.bring_to_front()
# 浏览器页面导航：通过真实页面加载触发Chrome环境、Cookie和JavaScript流程。
            history_page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.cfg.navigation_timeout_ms,
            )
            browser.page = history_page
            state, _ = browser.page_state()
            with self.lock:
                self.page_state = state
                self.manual_navigation_state = "complete" if state == "normal" else state
        except Exception as exc:
            with self.lock:
                self.manual_navigation_state = f"error:{type(exc).__name__}"
        finally:
            browser.close()


OPERATION_LABELS = {
    "self_check": "控制台自动自检",
    "validate": "校验配置",
    "capture": "捕获人工状态",
    "diagnose": "环境诊断",
    "probe": "单图片结构探针",
    "source_test": "Top-N 来源测试",
    "daily_run": "正式排名任务",
    "install": "安装或修复依赖",
}


# 功能：_bounded_text 函数，负责当前模块中的一项具体处理逻辑。
def _bounded_text(value: object, name: str, limit: int = 200) -> str:
    text = str(value or "").strip()
    if len(text) > limit or any(ord(char) < 32 for char in text):
        raise ValueError(f"invalid {name}")
    return text


# 类说明：OperationManager 封装相关业务状态和操作。
class OperationManager:
    """Run a fixed allowlist of local project operations without shell expansion."""

# 功能：__init__ 函数，负责当前模块中的一项具体处理逻辑。
    def __init__(self, root: Path, config_path: Path):
        self.root = root.resolve()
        self.config_path = config_path.resolve()
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.action = ""
        self.status = "idle"
        self.started_at: str | None = None
        self.finished_at: str | None = None
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
        self.return_code: int | None = None
        self.output = ""
        self.message = ""
        self.target_endpoint: str | None = None
        self.started_monotonic: float | None = None
        self.last_activity_at: str | None = None
        self.progress_current: int | None = None
        self.progress_total: int | None = None
        self.progress_status = ""

    @property
# 功能：active 函数，负责当前模块中的一项具体处理逻辑。
    def active(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

# 功能：生成 Dashboard展示所需状态快照。
# 包含：任务、Chrome、Clash等运行信息。

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "active": self.active,
                "action": self.action,
                "action_label": OPERATION_LABELS.get(self.action, self.action),
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "return_code": self.return_code,
                "output": self.output,
                "message": self.message,
                "target_endpoint": self.target_endpoint,
                "elapsed_seconds": (
                    int(time.monotonic() - self.started_monotonic)
                    if self.active and self.started_monotonic is not None
                    else None
                ),
                "last_activity_at": self.last_activity_at,
                "progress_current": self.progress_current,
                "progress_total": self.progress_total,
                "progress_status": self.progress_status,
            }

# 功能：_positive_int 函数，负责当前模块中的一项具体处理逻辑。
    def _positive_int(self, payload: dict, key: str, default: int, maximum: int) -> int:
        value = int(payload.get(key, default))
        if not 1 <= value <= maximum:
            raise ValueError(f"{key} must be between 1 and {maximum}")
        return value

# 功能：_command 函数，负责当前模块中的一项具体处理逻辑。
    def _command(self, action: str, payload: dict) -> list[str]:
        if action not in OPERATION_LABELS:
            raise ValueError("unknown operation")
        base = [sys.executable, "-m", "app.main", "--config", str(self.config_path)]
# 9222通常用于 Chrome DevTools Protocol(CDP) 调试连接端口。
        endpoint = validate_local_cdp_endpoint(str(payload.get("endpoint", "http://127.0.0.1:9222")))
        if action == "self_check":
            dashboard_base_url = validate_local_cdp_endpoint(
                str(payload.get("dashboard_base_url", "http://127.0.0.1:8765"))
            )
            return [
                sys.executable,
                "-m",
                "app.console_validator",
                "--base-url",
                dashboard_base_url,
                "--skip-operation",
            ]
        if action == "validate":
            return [*base, "--validate"]
        if action == "capture":
            return [*base, "--capture-state", "--cdp-endpoint", endpoint]
        if action == "diagnose":
            keyword = _bounded_text(payload.get("keyword", "Albert Einstein"), "keyword")
            return [*base, "--diagnose", "--diagnose-keyword", keyword, "--cdp-endpoint", endpoint]
        if action == "probe":
            keyword = _bounded_text(payload.get("keyword", "Albert Einstein"), "keyword")
            return [*base, "--probe-first-image", "--probe-keyword", keyword, "--cdp-endpoint", endpoint]
        if action == "source_test":
            limit = self._positive_int(payload, "limit", 10, 1000)
            top_n = self._positive_int(payload, "max_results", 100, 100)
            budget = self._positive_int(payload, "time_budget_seconds", 300, 86400)
            delay = float(payload.get("post_search_delay_seconds", 6))
            if not 0 <= delay <= 3600:
                raise ValueError("post_search_delay_seconds must be between 0 and 3600")
            return [
                *base,
                "--source-domain-test",
                "--limit",
                str(limit),
                "--test-max-results",
                str(top_n),
                "--test-time-budget-seconds",
                str(budget),
                "--test-post-search-delay-seconds",
                str(delay),
                "--cdp-endpoint",
                endpoint,
            ]
        if action == "daily_run":
            limit = self._positive_int(payload, "limit", 100, 1000)
            return [*base, "--limit", str(limit), "--cdp-endpoint", endpoint]
        if action == "install":
            if payload.get("confirmed") is not True:
                raise ValueError("dependency installation requires confirmation")
            return [sys.executable, "-m", "pip", "install", "-r", str(self.root / "requirements.txt")]
        raise ValueError("unknown operation")

# 功能：启动浏览器自动化环境。
# 位置：由搜索流程调用，负责建立 Browser / Context / Page 等 Playwright运行链路。

    def start(self, action: str, payload: dict) -> None:
        command = self._command(action, payload)
        with self.lock:
            if self.active:
                raise ValueError("another operation is already running")
            self.action = action
            self.status = "starting"
            self.started_at = datetime.now().isoformat(timespec="seconds")
            self.finished_at = None
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
            self.return_code = None
            self.output = ""
            self.message = ""
            self.target_endpoint = (
                validate_local_cdp_endpoint(str(payload["endpoint"]))
                if payload.get("endpoint")
                else None
            )
            self.started_monotonic = time.monotonic()
            self.last_activity_at = self.started_at
            self.progress_current = 0 if action in {"source_test", "daily_run"} else None
            self.progress_total = (
                int(payload.get("limit", 10)) if action in {"source_test", "daily_run"} else None
            )
            self.progress_status = "正在启动"
            self.worker = threading.Thread(
                target=self._run, args=(command,), name=f"operation-{action}", daemon=True
            )
            self.worker.start()

# 功能：Dashboard后台线程实际执行入口。

    def _run(self, command: list[str]) -> None:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            environment = dict(os.environ)
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONUNBUFFERED"] = "1"
            process = subprocess.Popen(
                command,
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
                env=environment,
            )
            with self.lock:
                self.process = process
                self.status = "running"
            assert process.stdout is not None
            for line in process.stdout:
                with self.lock:
                    self._consume_output_line(line)
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
            return_code = process.wait()
            with self.lock:
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                self.return_code = return_code
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                self.status = "complete" if return_code == 0 else "error"
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                self.message = "操作完成" if return_code == 0 else f"操作退出码：{return_code}"
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                if return_code == 0 and self.progress_total is not None:
                    self.progress_current = self.progress_total
                    self.progress_status = "已完成"
        except Exception as exc:
            with self.lock:
                self.status = "error"
                self.message = type(exc).__name__
        finally:
            with self.lock:
                self.process = None
                self.finished_at = datetime.now().isoformat(timespec="seconds")

# 功能：_consume_output_line 函数，负责当前模块中的一项具体处理逻辑。
    def _consume_output_line(self, line: str) -> None:
        """Update visible output and progress from one unbuffered child-process line."""
        self.last_activity_at = datetime.now().isoformat(timespec="seconds")
        structured = re.match(r"@@PROGRESS\s+(\d+)\s+(\d+)\s+(.+)", line.strip())
        logged = re.search(r"\[(\d+)/(\d+)\]", line)
        if structured:
            self.progress_current = int(structured.group(1))
            self.progress_total = int(structured.group(2))
            self.progress_status = structured.group(3)[:120]
            visible = (
                f"进度 {self.progress_current}/{self.progress_total}"
                f" · {self.progress_status}\n"
            )
            self.output = (self.output + visible)[-50000:]
            return
        if logged:
            self.progress_current = int(logged.group(1))
            self.progress_total = int(logged.group(2))
            self.progress_status = "正在处理"
        self.output = (self.output + line)[-50000:]

# 功能：停止正在运行的后台任务。

    def stop(self) -> None:
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                raise ValueError("no operation is running")
            self.status = "stopping"
            process.terminate()


# 类说明：DashboardManager 封装相关业务状态和操作。
class DashboardManager:
# 功能：__init__ 函数，负责当前模块中的一项具体处理逻辑。
    def __init__(self, cfg: Config, config_path: Path | None = None):
        self.cfg = cfg
        self.config_path = (config_path or Path("config.yaml")).resolve()
        self.root = self.config_path.parent
        self.registry_path = cfg.storage_state_path.parent / "dashboard_chromes.json"
        self.lock = threading.RLock()
        self.clash_context: dict[str, str | None] = {
            "clash_group": None,
            "clash_node": None,
        }
        self.slots: dict[str, ChromeSlot] = {}
        self.stop_event = threading.Event()
        self.operations = OperationManager(self.root, self.config_path)
        self.profile_manager = DedicatedChromeProfiles(self.root)
        self.secret_store = DashboardSecretStore(self.root / "private" / "dashboard_secrets.json")
        self.ip_database = IpIntelligenceDatabase(
            self.root / "private" / "ip_intelligence.sqlite3"
        )
        self.ip_reputation = IpReputationService(self.ip_database)
        self.ip_identity_service = IpIdentityService(
            self.ip_database, self.ip_reputation
        )
        self.ip_rotation_lock = threading.Lock()
        self._load_registry()
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

# 功能：_load_registry 函数，负责当前模块中的一项具体处理逻辑。
    def _load_registry(self) -> None:
        entries = []
        try:
            entries = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
        if not entries:
# 9222通常用于 Chrome DevTools Protocol(CDP) 调试连接端口。
            entries = [{"id": "chrome-9222", "label": "专用 Chrome 9222", "endpoint": self.cfg.cdp_endpoint}]
        for entry in entries:
            try:
                slot = ChromeSlot(str(entry["id"]), str(entry["label"]), str(entry["endpoint"]), self.cfg)
                slot.clash_context_provider = self.live_clash_context
                slot.ip_challenge_recorder = self.record_node_challenge
                self.slots[slot.id] = slot
            except Exception:
                continue

# 功能：_save_registry 函数，负责当前模块中的一项具体处理逻辑。
    def _save_registry(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {"id": slot.id, "label": slot.label, "endpoint": slot.endpoint}
            for slot in self.slots.values()
        ]
        temp = self.registry_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.registry_path)

# 功能：_monitor 函数，负责当前模块中的一项具体处理逻辑。
    def _monitor(self) -> None:
        while not self.stop_event.is_set():
            for slot in list(self.slots.values()):
                slot.probe()
            self.stop_event.wait(HUMAN_POLL_SECONDS)

# 功能：add_slot 函数，负责当前模块中的一项具体处理逻辑。
    def add_slot(self, label: str, endpoint: str) -> ChromeSlot:
        endpoint = validate_local_cdp_endpoint(endpoint)
        with self.lock:
            if any(slot.endpoint == endpoint for slot in self.slots.values()):
                raise ValueError("this CDP endpoint already exists")
            slot_id = "chrome-" + uuid.uuid4().hex[:8]
            slot = ChromeSlot(slot_id, label.strip() or endpoint, endpoint, self.cfg)
            slot.clash_context_provider = self.live_clash_context
            slot.ip_challenge_recorder = self.record_node_challenge
            self.slots[slot_id] = slot
            self._save_registry()
            slot.probe()
            return slot

# 功能：get 函数，负责当前模块中的一项具体处理逻辑。
    def get(self, slot_id: str | None) -> ChromeSlot:
        with self.lock:
            if slot_id and slot_id in self.slots:
                return self.slots[slot_id]
            if not self.slots:
                raise ValueError("no Chrome windows configured")
            return next(iter(self.slots.values()))

# 功能：获取 Clash 当前运行状态，包括代理组和节点信息。

    def status(self, slot_id: str | None) -> dict:
        selected = self.get(slot_id)
        data = selected.snapshot()
        selected_profile = next(
            (
                item
                for item in self.profile_manager.entries()
                if str(item.get("port", "")).isdigit()
# port表示网络端口，用于区分同一主机上的不同服务入口。
                and f"http://127.0.0.1:{int(item['port'])}" == selected.endpoint
            ),
            None,
        )
        data["proxy"] = {
            "proxy_url": "",
            "proxy_status": "unconfigured",
            "masked_ip": None,
            "proxy_latency_ms": None,
            "proxy_checked_at": None,
        }
        if selected_profile:
            data["proxy"].update(
                {
                    "proxy_url": str(selected_profile.get("proxy_url", "")),
                    "proxy_status": str(selected_profile.get("proxy_status", "direct")),
                    "masked_ip": selected_profile.get("masked_ip"),
                    "proxy_latency_ms": selected_profile.get("proxy_latency_ms"),
                    "proxy_checked_at": selected_profile.get("proxy_checked_at"),
                }
            )
            if selected.page_state in {"challenge", "consent"}:
                data["proxy"]["proxy_status"] = "verification_seen"
        data["chromes"] = [
            {
                "id": slot.id,
                "label": slot.label,
                "endpoint": slot.endpoint,
                "chrome_online": slot.chrome_online,
                "active": slot.active,
                "run_state": slot.run_state,
            }
            for slot in self.slots.values()
        ]
        data["selected_id"] = selected.id
        data["operation"] = self.operations.snapshot()
        return data

# 功能：profiles 函数，负责当前模块中的一项具体处理逻辑。
    def profiles(self) -> list[dict]:
        result = []
        for item in self.profile_manager.entries():
            if not str(item.get("port", "")).isdigit():
                continue
# port表示网络端口，用于区分同一主机上的不同服务入口。
            endpoint = f"http://127.0.0.1:{int(item.get('port', 0))}"
            slot = next((value for value in self.slots.values() if value.endpoint == endpoint), None)
            profile = {
                "name": str(item.get("name", "")),
                "port": int(item.get("port", 0)),
                "endpoint": endpoint,
                "online": endpoint_online(int(item.get("port", 0))),
                "proxy_url": str(item.get("proxy_url", "")),
                "proxy_status": str(item.get("proxy_status", "direct")),
                "masked_ip": item.get("masked_ip"),
                "proxy_latency_ms": item.get("proxy_latency_ms"),
                "proxy_checked_at": item.get("proxy_checked_at"),
                "restart_required": bool(item.get("restart_required", False)),
            }
            if slot and slot.page_state in {"challenge", "consent"}:
                profile["proxy_status"] = "verification_seen"
            result.append(profile)
        return result

# 功能：profile_suggestion 函数，负责当前模块中的一项具体处理逻辑。
    def profile_suggestion(self) -> dict:
        names = {str(item.get("name", "")) for item in self.profile_manager.entries()}
        index = 1
        while f"manual_{index:02d}" in names:
            index += 1
        return {
            "name": f"manual_{index:02d}",
            "port": self.profile_manager.suggest_port(),
            "url": DEFAULT_START_URL,
        }

# 功能：create_profile 函数，负责当前模块中的一项具体处理逻辑。
# port表示网络端口，用于区分同一主机上的不同服务入口。
    def create_profile(self, name: str, port: int | None, url: str, proxy_url: str = "") -> dict:
# port表示网络端口，用于区分同一主机上的不同服务入口。
        selected_port = port if port is not None else self.profile_manager.suggest_port()
        entry = self.profile_manager.create(
# port表示网络端口，用于区分同一主机上的不同服务入口。
            name, selected_port, proxy_url=proxy_url, register_dashboard=False
        )
# port表示网络端口，用于区分同一主机上的不同服务入口。
        endpoint = f"http://127.0.0.1:{entry['port']}"
        with self.lock:
            existing = next((slot for slot in self.slots.values() if slot.endpoint == endpoint), None)
            if existing:
                existing.label = f"专用 Chrome {entry['name']}"
                self._save_registry()
                slot = existing
            else:
                slot = self.add_slot(f"专用 Chrome {entry['name']}", endpoint)
        launch_state = self.profile_manager.launch(entry, url)
        return {"chrome_id": slot.id, "launch_state": launch_state, **entry}

# 功能：start_profile 函数，负责当前模块中的一项具体处理逻辑。
    def start_profile(self, name: str, url: str) -> dict:
        entry = self.profile_manager.get(name)
        return {"launch_state": self.profile_manager.launch(entry, url), **entry}

# 功能：set_profile_proxy 函数，负责当前模块中的一项具体处理逻辑。
    def set_profile_proxy(self, name: str, proxy_url: str) -> dict:
        entry = self.profile_manager.get(name)
# port表示网络端口，用于区分同一主机上的不同服务入口。
        online = endpoint_online(int(entry["port"]))
        updated = self.profile_manager.update_proxy(name, proxy_url)
        if online:
            updated = self.profile_manager.update_proxy_status(name, restart_required=True)
        return {"ok": True, "online": online, **updated}

# 功能：check_profile_proxy 函数，负责当前模块中的一项具体处理逻辑。
    def check_profile_proxy(self, name: str) -> dict:
        entry = self.profile_manager.get(name)
        proxy_url = str(entry.get("proxy_url", ""))
        if not proxy_url:
            return self.profile_manager.update_proxy_status(
                name,
                proxy_status="direct",
                masked_ip=None,
                proxy_latency_ms=None,
                proxy_checked_at=datetime.now().isoformat(timespec="seconds"),
            )
        try:
            result = masked_proxy_egress(proxy_url)
            self.profile_manager.update_proxy_status(
                name,
                proxy_status="online",
                masked_ip=result["masked_ip"],
                proxy_latency_ms=result["latency_ms"],
                proxy_checked_at=datetime.now().isoformat(timespec="seconds"),
            )
            return {"name": name, **result}
        except Exception:
            self.profile_manager.update_proxy_status(
                name,
                proxy_status="unreachable",
                masked_ip=None,
                proxy_latency_ms=None,
                proxy_checked_at=datetime.now().isoformat(timespec="seconds"),
            )
            raise

# 功能：start_operation 函数，负责当前模块中的一项具体处理逻辑。
    def start_operation(self, action: str, payload: dict) -> None:
        endpoint = payload.get("endpoint")
        if endpoint:
            normalized = validate_local_cdp_endpoint(str(endpoint))
            matching = next((slot for slot in self.slots.values() if slot.endpoint == normalized), None)
            if matching and matching.active:
                raise ValueError("selected Chrome already has an active dashboard task")
        self.operations.start(action, payload)

# 功能：recent_verification_events 函数，负责当前模块中的一项具体处理逻辑。
    def recent_verification_events(self, limit: int = 50) -> list[dict]:
        return CollectionTelemetry(telemetry_path(self.cfg.log_dir)).recent_events(
            max(1, min(int(limit), 200))
        )

# 功能：current_clash_context 函数，负责当前模块中的一项具体处理逻辑。
    def current_clash_context(self) -> dict[str, str | None]:
        with self.lock:
            return dict(self.clash_context)

# 功能：live_clash_context 函数，负责当前模块中的一项具体处理逻辑。
    def live_clash_context(self) -> dict[str, str | None]:
        """Refresh the active leaf only when a verification event needs attribution."""
        cached = self.current_clash_context()
        group = str(cached.get("clash_group") or "")
        if not group:
            return cached
        try:
            settings = self.secret_store.public_settings()
            controller = ClashController(
                str(settings["clash_endpoint"]), self.secret_store.clash_secret()
            )
            selector = next(
                (item for item in controller.selectors() if item.get("group") == group), None
            )
            if selector:
                with self.lock:
                    self.clash_context = {
                        "clash_group": group,
                        "clash_node": str(
                            selector.get("current_leaf") or selector.get("current", "")
                        ) or None,
                    }
        except Exception:
            return cached
        return self.current_clash_context()

# 功能：clash_status 函数，负责当前模块中的一项具体处理逻辑。
    def clash_status(self, endpoint: str, secret: str) -> dict:
        controller = ClashController(endpoint, secret)
        result = controller.status()
        selectors = result.get("selectors", [])
        with self.lock:
            known_group = self.clash_context.get("clash_group")
            selected = next(
                (item for item in selectors if item.get("group") == known_group),
                selectors[0] if selectors else None,
            )
            if selected:
                self.clash_context = {
                    "clash_group": str(selected.get("group", "")) or None,
                    "clash_node": str(
                        selected.get("current_leaf") or selected.get("current", "")
                    ) or None,
                }
        return result

# 功能：switch_clash 函数，负责当前模块中的一项具体处理逻辑。
    def switch_clash(self, endpoint: str, secret: str, group: str, node: str) -> dict:
        controller = ClashController(endpoint, secret)
        result = controller.switch(group, node)
        selector = next(
            (item for item in controller.selectors() if item.get("group") == group), None
        )
        current_leaf = str((selector or {}).get("current_leaf") or node)
        with self.lock:
            self.clash_context = {"clash_group": group, "clash_node": current_leaf}
        return result

# 功能：probe_clash_nodes 函数，负责当前模块中的一项具体处理逻辑。
    def probe_clash_nodes(
        self, endpoint: str, secret: str, group: str, timeout_ms: int, max_nodes: int
    ) -> dict:
        controller = ClashController(endpoint, secret)
        result = controller.probe_group_nodes(
            group, timeout_ms=timeout_ms, max_nodes=max_nodes
        )
        selector = next(
            (item for item in controller.selectors() if item.get("group") == group), None
        )
        if selector:
            with self.lock:
                self.clash_context = {
                    "clash_group": group,
                    "clash_node": str(
                        selector.get("current_leaf") or selector.get("current", "")
                    ) or None,
                }
        latest_by_node: dict[str, str] = {}
        for event in self.recent_verification_events(200):
            node = str(event.get("clash_node") or "")
            if node and node not in latest_by_node:
                latest_by_node[node] = str(event.get("occurred_at") or "")
        for item in result["nodes"]:
            item["last_verification_at"] = latest_by_node.get(str(item["name"]))
        intelligence_service = getattr(self, "ip_identity_service", None)
        intelligence = {
            item["node"]: item for item in intelligence_service.dashboard_rows()
        } if intelligence_service is not None else {}
        for item in result["nodes"]:
            details = intelligence.get(str(item["name"]), {})
            item.update({
                "masked_ip": details.get("masked_ip"),
                "country": details.get("country"),
                "ip_status": details.get("status", "UNKNOWN"),
                "ip_score": details.get("ip_score"),
                "shared_egress": bool(details.get("shared_egress", False)),
                "challenge_count": details.get("challenge", 0),
            })
        return result

    def ip_intelligence(self) -> list[dict]:
        service = getattr(self, "ip_identity_service", None)
        return service.dashboard_rows() if service is not None else []

    def observe_clash_egress(self, proxy_url: str, *, collector=None) -> dict:
        """Collect a consensus Full IP, persist its node binding, and return only public fields."""
        proxy_url = validate_local_http_url(proxy_url, "proxy URL")
        before_context = self.live_clash_context()
        sample = (collector or FullIpCollector()).collect(proxy_url)
        after_context = self.live_clash_context()
        before_identity = (
            str(before_context.get("clash_group") or ""),
            str(before_context.get("clash_node") or ""),
        )
        after_identity = (
            str(after_context.get("clash_group") or ""),
            str(after_context.get("clash_node") or ""),
        )
        attribution_stable = before_identity == after_identity
        group = after_identity[0] or None
        node = after_identity[1] or None
        recorded = False
        if sample.get("ok") and sample.get("full_ip") and node and attribution_stable:
            node_type = None
            secret_store = getattr(self, "secret_store", None)
            if secret_store is not None:
                try:
                    settings = secret_store.public_settings()
                    controller = ClashController(
                        str(settings["clash_endpoint"]), secret_store.clash_secret()
                    )
                    node_type = str(
                        controller.get_proxy_detail_v21(node).get("type") or ""
                    ) or None
                except Exception:
                    # Full-IP identity remains useful if topology metadata is unavailable.
                    node_type = None
            self.ip_identity_service.observe(
                node,
                str(sample["full_ip"]),
                country=sample.get("country"),
                node_type=node_type,
            )
            recorded = True
        return {
            "ok": bool(sample.get("ok")),
            "error_code": sample.get("error_code"),
            "family": sample.get("family"),
            "masked_ip": mask_full_ip(sample.get("full_ip")),
            "country": sample.get("country"),
            "consensus": int(sample.get("consensus") or 0),
            "consensus_required": int(sample.get("consensus_required") or 0),
            "providers_ok": int(sample.get("providers_ok") or 0),
            "latency_ms": sample.get("latency_ms"),
            "collected_at": sample.get("collected_at"),
            "group": group,
            "node": node,
            "attribution_stable": attribution_stable,
            "recorded": recorded,
        }

    def record_node_challenge(self, node: str | None) -> None:
        if not node:
            return
        database = getattr(self, "ip_database", None)
        reputation = getattr(self, "ip_reputation", None)
        if database is None or reputation is None:
            return
        identity = database.latest_ip_for_node(str(node))
        if identity and identity.get("full_ip"):
            reputation.mark_challenge(str(identity["full_ip"]), node=str(node))

    def rotate_clash(self, endpoint: str, secret: str, proxy_url: str,
                     group: str, cdp_endpoint: str | None = None,
                     max_attempts: int | None = None) -> dict:
        endpoint = validate_local_http_url(endpoint, "controller endpoint")
        proxy_url = validate_local_http_url(proxy_url, "proxy URL")
        if not group.strip():
            raise ValueError("group is required")
        if max_attempts is not None and not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        if not self.ip_rotation_lock.acquire(blocking=False):
            return {"ok": False, "error_code": "ROTATION_BUSY", "state": "ROTATING"}
        try:
            history = IpHistoryStore(self.root / "private" / "ip_history.json")
            scores = NodeScoreStore(self.root / "private" / "node_score.json")
            decision = IpDecisionEngine(history, scores, self.ip_identity_service)
            rotator = ClashIpRotator(
                endpoint, secret, proxy_url, decision_engine=decision,
                identity_service=self.ip_identity_service,
                database=self.ip_database,
            )
            result = rotator.rotate(group=group, max_attempts=max_attempts)
            if result.get("ok"):
                with self.lock:
                    self.clash_context = {
                        "clash_group": group,
                        "clash_node": result.get("node"),
                    }
                expected = (result.get("new_ip") or {}).get("full_ip")
                if cdp_endpoint:
                    result["chrome_verification"] = ChromeIpVerifier().verify(
                        validate_local_cdp_endpoint(cdp_endpoint), expected
                    )
            return self._public_ip_result(result)
        finally:
            self.ip_rotation_lock.release()

    @classmethod
    def _public_ip_result(cls, value):
        if isinstance(value, list):
            return [cls._public_ip_result(item) for item in value]
        if not isinstance(value, dict):
            return value
        public = {}
        for key, item in value.items():
            if key == "full_ip":
                public.setdefault("masked_ip", mask_full_ip(str(item)) if item else None)
                continue
            public[key] = cls._public_ip_result(item)
        return public


# 类说明：DashboardHandler 封装相关业务状态和操作。
class DashboardHandler(BaseHTTPRequestHandler):
    server: "DashboardServer"

# 功能：log_message 函数，负责当前模块中的一项具体处理逻辑。
    def log_message(self, _format, *_args):
        return

# 功能：_json 函数，负责当前模块中的一项具体处理逻辑。
    def _json(self, payload: dict, status: int = 200) -> None:
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

# 功能：_body 函数，负责当前模块中的一项具体处理逻辑。
    def _body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 16384)
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

# 功能：do_GET 函数，负责当前模块中的一项具体处理逻辑。
    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == "/":
            body = (Path(__file__).with_name("dashboard.html")).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parts.path == "/api/status":
            slot_id = parse_qs(parts.query).get("chrome_id", [None])[0]
            self._json(self.server.manager.status(slot_id))
            return
        if parts.path == "/api/profiles":
            self._json({"profiles": self.server.manager.profiles()})
            return
        if parts.path == "/api/profiles/suggest":
            self._json(self.server.manager.profile_suggestion())
            return
        if parts.path == "/api/operations":
            self._json(self.server.manager.operations.snapshot())
            return
        if parts.path == "/api/settings":
            self._json(self.server.manager.secret_store.public_settings())
            return
        if parts.path == "/api/verification-events":
            raw_limit = parse_qs(parts.query).get("limit", ["50"])[0]
            self._json(
                {"events": self.server.manager.recent_verification_events(int(raw_limit))}
            )
            return
        if parts.path == "/api/ip-intelligence":
            self._json({"nodes": self.server.manager.ip_intelligence()})
            return
        self._json({"error": "not found"}, 404)

# 功能：do_POST 函数，负责当前模块中的一项具体处理逻辑。
    def do_POST(self):
        try:
            payload = self._body()
            if self.path == "/api/chromes":
                slot = self.server.manager.add_slot(
                    str(payload.get("label", "")), str(payload.get("endpoint", ""))
                )
                self._json({"ok": True, "chrome_id": slot.id})
                return
            if self.path == "/api/profiles/create":
# port表示网络端口，用于区分同一主机上的不同服务入口。
                raw_port = payload.get("port")
                result = self.server.manager.create_profile(
                    str(payload.get("name", "")),
                    int(raw_port) if raw_port not in {None, ""} else None,
                    str(payload.get("url", DEFAULT_START_URL)),
                    str(payload.get("proxy_url", "")),
                )
                self._json({"ok": True, **result})
                return
            if self.path == "/api/profiles/start":
                result = self.server.manager.start_profile(
                    str(payload.get("name", "")),
                    str(payload.get("url", DEFAULT_START_URL)),
                )
                self._json({"ok": True, **result})
                return
            if self.path == "/api/profiles/proxy":
                result = self.server.manager.set_profile_proxy(
                    str(payload.get("name", "")), str(payload.get("proxy_url", ""))
                )
                self._json(result)
                return
            if self.path == "/api/profiles/proxy-check":
                result = self.server.manager.check_profile_proxy(str(payload.get("name", "")))
                self._json({"ok": True, **result})
                return
            if self.path == "/api/operations/start":
                self.server.manager.start_operation(str(payload.get("action", "")), payload)
                self._json({"ok": True})
                return
            if self.path == "/api/operations/stop":
                self.server.manager.operations.stop()
                self._json({"ok": True})
                return
            if self.path == "/api/clash/status":
                secret = self.server.manager.secret_store.clash_secret(str(payload.get("secret", "")))
                self._json(self.server.manager.clash_status(
                    str(payload.get("endpoint", "http://127.0.0.1:9097")),
                    secret,
                ))
                return
            if self.path == "/api/clash/switch":
                secret = self.server.manager.secret_store.clash_secret(str(payload.get("secret", "")))
                result = self.server.manager.switch_clash(
                    str(payload.get("endpoint", "http://127.0.0.1:9097")),
                    secret,
                    str(payload.get("group", "")),
                    str(payload.get("node", "")),
                )
                self._json(result)
                return
            if self.path == "/api/clash/nodes/probe":
                secret = self.server.manager.secret_store.clash_secret(str(payload.get("secret", "")))
                result = self.server.manager.probe_clash_nodes(
                    str(payload.get("endpoint", "http://127.0.0.1:9097")),
                    secret,
                    str(payload.get("group", "")),
                    int(payload.get("timeout_ms", 3000)),
                    int(payload.get("max_nodes", 200)),
                )
                self._json(result)
                return
            if self.path == "/api/clash/egress":
                self._json(self.server.manager.observe_clash_egress(
                    str(payload.get("proxy_url", "http://127.0.0.1:7897"))
                ))
                return
            if self.path == "/api/clash/rotate":
                secret = self.server.manager.secret_store.clash_secret(str(payload.get("secret", "")))
                raw_attempts = payload.get("max_attempts")
                self._json(self.server.manager.rotate_clash(
                    str(payload.get("endpoint", "http://127.0.0.1:9097")),
                    secret,
                    str(payload.get("proxy_url", "http://127.0.0.1:7897")),
                    str(payload.get("group", "")),
                    str(payload.get("cdp_endpoint", "")) or None,
                    int(raw_attempts) if raw_attempts not in {None, ""} else None,
                ))
                return
            if self.path == "/api/settings/clash/save":
                secret = validate_secret(str(payload.get("secret", "")))
                endpoint = validate_local_http_url(
                    str(payload.get("endpoint", "http://127.0.0.1:9097")), "controller endpoint"
                )
                proxy_url = validate_local_http_url(
                    str(payload.get("proxy_url", "http://127.0.0.1:7897")), "proxy URL"
                )
                self.server.manager.secret_store.save_clash(secret, endpoint, proxy_url)
                self._json({"ok": True})
                return
            if self.path == "/api/settings/clash/clear":
                self.server.manager.secret_store.clear_clash()
                self._json({"ok": True})
                return
            slot = self.server.manager.get(str(payload.get("chrome_id", "")) or None)
            if self.path == "/api/start":
                operation = self.server.manager.operations.snapshot()
                if operation["active"] and operation["target_endpoint"] == slot.endpoint:
                    raise ValueError("selected Chrome is busy with another operation")
                limit = int(payload.get("limit", 50))
                max_results = int(payload.get("max_results", 100))
                delay = float(payload.get("post_search_delay_seconds", 6))
                start_index = int(payload.get("start_index", 1))
                if not (1 <= limit <= 1000):
                    raise ValueError("limit must be between 1 and 1000")
                if not (1 <= max_results <= 100) or delay < 0:
                    raise ValueError("invalid max_results or delay")
                slot.start_run(limit, max_results, delay, start_index)
            elif self.path == "/api/stop":
                slot.stop()
            elif self.path == "/api/history":
                slot.navigate_for_history(str(payload.get("url", "")))
            else:
                self._json({"error": "not found"}, 404)
                return
            self._json({"ok": True})
        except Exception as exc:
            self._json({"error": str(exc)}, 400)


# 类说明：DashboardServer 封装相关业务状态和操作。
class DashboardServer(ThreadingHTTPServer):
# 功能：__init__ 函数，负责当前模块中的一项具体处理逻辑。
    def __init__(self, address, manager: DashboardManager):
        super().__init__(address, DashboardHandler)
        self.manager = manager


# 功能：main 函数，负责当前模块中的一项具体处理逻辑。
def main() -> None:
    parser = argparse.ArgumentParser(description="Local Chrome monitoring dashboard")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
# port表示网络端口，用于区分同一主机上的不同服务入口。
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in LOCAL_CDP_HOSTS:
        raise SystemExit("dashboard host must be loopback-only")
    cfg = load_config(args.config)
    manager = DashboardManager(cfg, Path(args.config))
# port表示网络端口，用于区分同一主机上的不同服务入口。
    server = DashboardServer((args.host, args.port), manager)
# port表示网络端口，用于区分同一主机上的不同服务入口。
    url = f"http://{args.host}:{args.port}/"
    print(f"Dashboard: {url}")
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
