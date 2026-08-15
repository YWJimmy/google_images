from __future__ import annotations

import argparse
from dataclasses import replace
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
import uuid
import webbrowser

from .config import Config, load_config
from .error_codes import describe
from .google_images import (
    BrowserLaunchError,
    ChallengeDetected,
    ConsentRequired,
    GoogleImagesBrowser,
    NavigationError,
    SearchParseTimeout,
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
    "timeout": "5秒超时跳过",
    "navigation_error": "导航错误",
    "error": "内部错误",
    "stopped": "已停止",
    "resumed": "此前已完成",
}


def validate_local_cdp_endpoint(endpoint: str) -> str:
    value = endpoint.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme != "http" or parts.hostname not in LOCAL_CDP_HOSTS or not parts.port:
        raise ValueError("CDP endpoint must be a local http URL with an explicit port")
    return value


def validate_history_url(url: str) -> str:
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("history URL must be an absolute http(s) URL")
    return value


def classify_cdp_page_urls(urls: list[str]) -> str:
    """Classify local CDP tab URLs without retaining query strings or page content."""
    saw_google = False
    for value in urls:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        path = parts.path.lower()
        if is_google_host(host):
            saw_google = True
        if is_google_host(host) and "/sorry/" in path:
            return "challenge"
        if host.startswith("consent.google."):
            return "consent"
    if saw_google:
        return "normal"
    return "other" if urls else "no_pages"


class ChromeSlot:
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

    @property
    def active(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def probe(self) -> None:
        online = False
        page_state = "unknown"
        try:
            with urlopen(self.endpoint + "/json/version", timeout=2) as response:
                online = response.status == 200
            with urlopen(self.endpoint + "/json", timeout=2) as response:
                targets = json.loads(response.read().decode("utf-8"))
                page_state = classify_cdp_page_urls(
                    [str(target.get("url", "")) for target in targets if target.get("type") == "page"]
                )
        except Exception:
            online = False
        with self.lock:
            self.chrome_online = online
            self.last_probe_at = time.time()
            if not self.active:
                self.page_state = page_state

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
                "tasks": [dict(task) for task in self.tasks],
            }

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
            self.worker = threading.Thread(
                target=self._run,
                args=(source_tasks, max_results, post_delay, start_index),
                name=f"chrome-run-{self.id}",
                daemon=True,
            )
            self.worker.start()

    def stop(self) -> None:
        with self.lock:
            if self.active:
                self.run_state = "stopping"
                self.stop_event.set()

    def _set_task(self, index: int, **changes) -> None:
        with self.lock:
            task = self.tasks[index - 1]
            task.update(changes)
            status = str(task.get("status", "pending"))
            task["status_label"] = TASK_LABELS.get(status, status)

    def _wait_for_human(self, browser: GoogleImagesBrowser) -> bool:
        while not self.stop_event.is_set():
            with self.lock:
                self.next_human_poll_at = time.time() + HUMAN_POLL_SECONDS
            if self.stop_event.wait(HUMAN_POLL_SECONDS):
                return False
            try:
                browser.refresh_page_binding(prefer_normal_google_page=True)
                page_state, _ = browser.page_state()
            except Exception:
                page_state = "unknown"
            with self.lock:
                self.page_state = page_state
                self.next_human_poll_at = None
            if page_state == "normal":
                return True
        return False

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
                    self._set_task(index, status="searching", attempts=self.tasks[index - 1]["attempts"] + 1)
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
                        )
                        with self.lock:
                            self.page_state = "normal"
                        break
                    except (ChallengeDetected, ConsentRequired) as exc:
                        waiting_state = "challenge" if isinstance(exc, ChallengeDetected) else "consent"
                        self._set_task(index, status="waiting_for_human", message=str(exc))
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
                        self._set_task(
                            index,
                            status="timeout",
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            message=str(exc),
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

    def _navigate_for_history(self, url: str) -> None:
        browser = GoogleImagesBrowser(
            replace(self.cfg, session_mode="manual_cdp", cdp_endpoint=self.endpoint)
        )
        try:
            browser.start()
            history_page = browser.context.new_page()
            history_page.bring_to_front()
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


class DashboardManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.registry_path = cfg.storage_state_path.parent / "dashboard_chromes.json"
        self.lock = threading.RLock()
        self.slots: dict[str, ChromeSlot] = {}
        self.stop_event = threading.Event()
        self._load_registry()
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

    def _load_registry(self) -> None:
        entries = []
        try:
            entries = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
        if not entries:
            entries = [{"id": "chrome-9222", "label": "专用 Chrome 9222", "endpoint": self.cfg.cdp_endpoint}]
        for entry in entries:
            try:
                slot = ChromeSlot(str(entry["id"]), str(entry["label"]), str(entry["endpoint"]), self.cfg)
                self.slots[slot.id] = slot
            except Exception:
                continue

    def _save_registry(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {"id": slot.id, "label": slot.label, "endpoint": slot.endpoint}
            for slot in self.slots.values()
        ]
        temp = self.registry_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.registry_path)

    def _monitor(self) -> None:
        while not self.stop_event.is_set():
            for slot in list(self.slots.values()):
                slot.probe()
            self.stop_event.wait(HUMAN_POLL_SECONDS)

    def add_slot(self, label: str, endpoint: str) -> ChromeSlot:
        endpoint = validate_local_cdp_endpoint(endpoint)
        with self.lock:
            if any(slot.endpoint == endpoint for slot in self.slots.values()):
                raise ValueError("this CDP endpoint already exists")
            slot_id = "chrome-" + uuid.uuid4().hex[:8]
            slot = ChromeSlot(slot_id, label.strip() or endpoint, endpoint, self.cfg)
            self.slots[slot_id] = slot
            self._save_registry()
            slot.probe()
            return slot

    def get(self, slot_id: str | None) -> ChromeSlot:
        with self.lock:
            if slot_id and slot_id in self.slots:
                return self.slots[slot_id]
            if not self.slots:
                raise ValueError("no Chrome windows configured")
            return next(iter(self.slots.values()))

    def status(self, slot_id: str | None) -> dict:
        selected = self.get(slot_id)
        data = selected.snapshot()
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
        return data


class DashboardHandler(BaseHTTPRequestHandler):
    server: "DashboardServer"

    def log_message(self, _format, *_args):
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 16384)
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

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
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            payload = self._body()
            if self.path == "/api/chromes":
                slot = self.server.manager.add_slot(
                    str(payload.get("label", "")), str(payload.get("endpoint", ""))
                )
                self._json({"ok": True, "chrome_id": slot.id})
                return
            slot = self.server.manager.get(str(payload.get("chrome_id", "")) or None)
            if self.path == "/api/start":
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


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, address, manager: DashboardManager):
        super().__init__(address, DashboardHandler)
        self.manager = manager


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Chrome monitoring dashboard")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in LOCAL_CDP_HOSTS:
        raise SystemExit("dashboard host must be loopback-only")
    cfg = load_config(args.config)
    manager = DashboardManager(cfg)
    server = DashboardServer((args.host, args.port), manager)
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
