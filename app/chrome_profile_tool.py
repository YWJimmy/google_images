from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen


DEFAULT_START_URL = "https://images.google.com/ncr"
PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}\Z")
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def validate_profile_name(value: str) -> str:
    name = value.strip()
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise ValueError("名称须为 1–40 位字母、数字、下划线或连字符，并以字母或数字开头")
    if name.upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError("该名称是 Windows 保留名称，请换一个")
    return name


def validate_port(value: int | str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise ValueError("调试端口须在 1024–65535 之间")
    return port


def validate_start_url(value: str) -> str:
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("起始网址须为完整的 http(s) URL")
    return url


def find_chrome() -> Path:
    candidates = []
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未在标准安装位置找到 Google Chrome")


def endpoint_online(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def port_available(port: int) -> bool:
    if endpoint_online(port):
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _read_json_list(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _write_json_private(path: Path, value: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class DedicatedChromeProfiles:
    def __init__(self, root: Path | None = None):
        self.root = (root or project_root()).resolve()
        self.profiles_root = (self.root / "profile" / "dedicated").resolve()
        self.manifest_path = self.root / "private" / "dedicated_chrome_profiles.json"
        self.dashboard_registry_path = self.root / "private" / "dashboard_chromes.json"

    def entries(self) -> list[dict]:
        return _read_json_list(self.manifest_path)

    def suggest_port(self) -> int:
        reserved = {int(item["port"]) for item in self.entries() if str(item.get("port", "")).isdigit()}
        for port in range(9222, 9323):
            if port not in reserved and port_available(port):
                return port
        raise RuntimeError("9222–9322 范围内没有可用调试端口")

    def get(self, name: str) -> dict:
        normalized = validate_profile_name(name)
        for item in self.entries():
            if item.get("name") == normalized:
                return item
        raise KeyError(f"未找到专用 Profile：{normalized}")

    def create(self, name: str, port: int, *, register_dashboard: bool = True) -> dict:
        normalized = validate_profile_name(name)
        validated_port = validate_port(port)
        entries = self.entries()
        if any(item.get("name") == normalized for item in entries):
            raise ValueError(f"Profile 已存在：{normalized}；请使用启动功能")
        if any(int(item.get("port", -1)) == validated_port for item in entries):
            raise ValueError(f"端口已分配给其他专用 Profile：{validated_port}")
        if not port_available(validated_port):
            raise ValueError(f"端口当前不可用：{validated_port}")

        profile_dir = (self.profiles_root / normalized).resolve()
        if not profile_dir.is_relative_to(self.profiles_root):
            raise ValueError("Profile 路径越界")
        profile_dir.mkdir(parents=True, exist_ok=False)
        entry = {
            "name": normalized,
            "port": validated_port,
            "profile_dir": str(profile_dir.relative_to(self.root)),
        }
        entries.append(entry)
        _write_json_private(self.manifest_path, entries)
        if register_dashboard:
            self.register_dashboard(entry)
        return entry

    def register_dashboard(self, entry: dict) -> None:
        endpoint = f"http://127.0.0.1:{entry['port']}"
        entries = _read_json_list(self.dashboard_registry_path)
        existing = next((item for item in entries if item.get("endpoint") == endpoint), None)
        if existing:
            existing["label"] = f"专用 Chrome {entry['name']}"
        else:
            entries.append(
                {
                    "id": f"chrome-{entry['port']}",
                    "label": f"专用 Chrome {entry['name']}",
                    "endpoint": endpoint,
                }
            )
        _write_json_private(self.dashboard_registry_path, entries)

    def launch(self, entry: dict, start_url: str = DEFAULT_START_URL) -> str:
        url = validate_start_url(start_url)
        port = validate_port(entry["port"])
        profile_dir = (self.root / str(entry["profile_dir"])).resolve()
        if not profile_dir.is_relative_to(self.profiles_root):
            raise ValueError("Profile 路径越界")
        profile_dir.mkdir(parents=True, exist_ok=True)
        chrome = find_chrome()
        already_running = endpoint_online(port)
        args = [
            str(chrome),
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if not already_running:
            if not port_available(port):
                raise ValueError(f"端口被其他程序占用：{port}")
            args.extend(
                [
                    f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1",
                    "--new-window",
                ]
            )
        args.append(url)
        subprocess.Popen(args, cwd=str(self.root))
        return "already_running" if already_running else "started"


def _print_entries(manager: DedicatedChromeProfiles) -> None:
    entries = manager.entries()
    if not entries:
        print("尚未创建专用 Chrome Profile。")
        return
    print("\n名称\t端口\t状态\tProfile 目录")
    for item in entries:
        status = "在线" if endpoint_online(int(item["port"])) else "离线"
        print(f"{item['name']}\t{item['port']}\t{status}\t{item['profile_dir']}")


def interactive(manager: DedicatedChromeProfiles) -> int:
    print("专用 Chrome Profile 工具")
    print("1. 创建并打开  2. 打开已有  3. 列出")
    action = input("请选择 [1]：").strip() or "1"
    if action == "3":
        _print_entries(manager)
        return 0
    if action == "2":
        _print_entries(manager)
        name = input("Profile 名称：").strip()
        entry = manager.get(name)
    elif action == "1":
        name = input("新 Profile 名称（字母/数字/_/-）：").strip()
        suggested = manager.suggest_port()
        port_text = input(f"本机调试端口 [{suggested}]：").strip()
        entry = manager.create(name, validate_port(port_text or suggested))
        print("已创建隔离 Profile，并登记到本地监控列表。")
    else:
        raise ValueError("未知选项")
    url = input(f"手动访问的起始网址 [{DEFAULT_START_URL}]：").strip() or DEFAULT_START_URL
    state = manager.launch(entry, url)
    print(f"Chrome 已打开；Profile={entry['name']}，CDP=http://127.0.0.1:{entry['port']}，状态={state}")
    print("请在该窗口中手动浏览。不要将 profile/ 或 private/ 中的文件分享或提交。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="创建和启动隔离的可见 Chrome Profile")
    sub = parser.add_subparsers(dest="command")
    create = sub.add_parser("create", help="创建并打开新 Profile")
    create.add_argument("--name", required=True)
    create.add_argument("--port", type=int)
    create.add_argument("--url", default=DEFAULT_START_URL)
    start = sub.add_parser("start", help="打开已有 Profile")
    start.add_argument("--name", required=True)
    start.add_argument("--url", default=DEFAULT_START_URL)
    sub.add_parser("list", help="列出本地 Profile")
    sub.add_parser("interactive", help="打开交互式菜单")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manager = DedicatedChromeProfiles()
    try:
        if args.command in {None, "interactive"}:
            return interactive(manager)
        if args.command == "list":
            _print_entries(manager)
            return 0
        if args.command == "create":
            port = args.port if args.port is not None else manager.suggest_port()
            entry = manager.create(args.name, port)
            manager.launch(entry, args.url)
            print(f"已创建并打开：{entry['name']}，CDP=http://127.0.0.1:{entry['port']}")
            return 0
        if args.command == "start":
            entry = manager.get(args.name)
            manager.launch(entry, args.url)
            print(f"已打开：{entry['name']}，CDP=http://127.0.0.1:{entry['port']}")
            return 0
    except (ValueError, KeyError, FileNotFoundError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
