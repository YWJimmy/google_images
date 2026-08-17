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


# 默认打开 Google 图片搜索入口；/ncr 用于避免根据访问位置自动跳转到其他国家/地区域名。
# 下面两个常量分别约束 Profile 名称格式，以及排除 Windows 不能作为文件/目录名使用的保留名称。
DEFAULT_START_URL = "https://images.google.com/ncr"
PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}\Z")
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
}

# 返回项目根目录。当前文件位于 app/ 下，因此 parents[1] 即仓库根目录。
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


# 校验专用 Chrome Profile 的逻辑名称，防止非法字符、过长名称以及 Windows 保留名称进入文件路径。
def validate_profile_name(value: str) -> str:
    name = value.strip()
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise ValueError("名称须为 1–40 位字母、数字、下划线或连字符，并以字母或数字开头")
    if name.upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError("该名称是 Windows 保留名称，请换一个")
    return name

# 把输入转换为整数端口，并限制在 1024–65535；这里的端口用于 Chrome DevTools Protocol（CDP）调试接口。
def validate_port(value: int | str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise ValueError("调试端口须在 1024–65535 之间")
    return port


# 校验启动 Chrome 后要打开的网址，只接受包含主机名的完整 http/https URL。
def validate_start_url(value: str) -> str:
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("起始网址须为完整的 http(s) URL")
    return url

# 校验某个 Profile 使用的代理地址。
# 这里只允许无用户名/密码的本机回环 HTTP 代理，例如 http://127.0.0.1:7890，避免把远程代理地址直接写入 Profile 配置。
def validate_profile_proxy(value: str) -> str:
    """Allow direct access or an unauthenticated loopback HTTP proxy."""
    proxy = value.strip().rstrip("/")
    if not proxy:
        return ""
    parts = urlsplit(proxy)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parts.port
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Profile proxy must be an unauthenticated local http URL with a port")
    return proxy

# 在 Windows 常见安装目录中查找 chrome.exe；依次检查 Program Files、Program Files (x86) 和当前用户 LocalAppData。
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

# 通过访问 Chrome CDP 的 /json/version 判断指定调试端口是否已经存在可用 Chrome 实例。
# 127.0.0.1 是本机回环地址，请求不会发往局域网或公网。
def endpoint_online(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False

# 检查本机回环地址上的 TCP 端口能否被当前程序独占绑定。
# 先排除已经存在 CDP 服务的情况，再用 socket.bind 实际验证端口是否被其他进程占用。
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

# 读取内部 JSON 列表文件。文件不存在、JSON 损坏或读取失败时返回空列表，使调用方可以按“尚未配置”处理。
def _read_json_list(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

# 把内部配置安全地写回 JSON：先写 .tmp 临时文件，再 replace 原文件，减少写入中断导致半截 JSON 的风险。
def _write_json_private(path: Path, value: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

# 管理项目专用的、彼此隔离的 Chrome 用户数据目录以及对应的 CDP 端口、代理配置和控制台登记信息。
class DedicatedChromeProfiles:
    # 确定项目根目录，并固定 Profile 数据目录、Profile 清单文件和 dashboard Chrome 注册表的位置。
    def __init__(self, root: Path | None = None):
        self.root = (root or project_root()).resolve()
        self.profiles_root = (self.root / "profile" / "dedicated").resolve()
        self.manifest_path = self.root / "private" / "dedicated_chrome_profiles.json"
        self.dashboard_registry_path = self.root / "private" / "dashboard_chromes.json"

    # 读取所有已登记的专用 Chrome Profile。
    def entries(self) -> list[dict]:
        return _read_json_list(self.manifest_path)
    # 在 9222–9322 中寻找一个既没有被清单占用、当前系统也可绑定的 CDP 端口。
    # 9222 是 Chrome 远程调试常用起始端口，这里向后预留连续范围供多个专用 Profile 使用。
    def suggest_port(self) -> int:
        reserved = {int(item["port"]) for item in self.entries() if str(item.get("port", "")).isdigit()}
        for port in range(9222, 9323):
            if port not in reserved and port_available(port):
                return port
        raise RuntimeError("9222–9322 范围内没有可用调试端口")
    # 按 Profile 名称从清单中查找记录；找不到时抛出 KeyError。
    def get(self, name: str) -> dict:
        normalized = validate_profile_name(name)
        for item in self.entries():
            if item.get("name") == normalized:
                return item
        raise KeyError(f"未找到专用 Profile：{normalized}")
    # 创建一个新的专用 Profile 记录：校验名称和端口、创建独立 user-data-dir、保存代理配置，并可登记到 dashboard。
    def create(
        self, name: str, port: int, *, proxy_url: str = "", register_dashboard: bool = True
    ) -> dict:
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
            "proxy_url": validate_profile_proxy(proxy_url),
            "proxy_status": "unchecked" if proxy_url.strip() else "direct",
        }
        entries.append(entry)
        _write_json_private(self.manifest_path, entries)
        if register_dashboard:
            self.register_dashboard(entry)
        return entry
    # 更新指定 Profile 的本地代理地址，同时清空上一次代理检测结果，等待后续重新检查。
    def update_proxy(self, name: str, proxy_url: str) -> dict:
        normalized = validate_profile_name(name)
        value = validate_profile_proxy(proxy_url)
        entries = self.entries()
        entry = next((item for item in entries if item.get("name") == normalized), None)
        if entry is None:
            raise KeyError(f"Profile not found: {normalized}")
        entry.update(
            {
                "proxy_url": value,
                "proxy_status": "unchecked" if value else "direct",
                "masked_ip": None,
                "proxy_latency_ms": None,
                "proxy_checked_at": None,
                "restart_required": False,
            }
        )
        _write_json_private(self.manifest_path, entries)
        return dict(entry)
    # 只更新 Profile 的代理状态字段，例如脱敏 IP、延迟、检测时间或是否需要重启，不改变代理地址本身。
    def update_proxy_status(self, name: str, **status) -> dict:
        normalized = validate_profile_name(name)
        entries = self.entries()
        entry = next((item for item in entries if item.get("name") == normalized), None)
        if entry is None:
            raise KeyError(f"Profile not found: {normalized}")
        entry.update(status)
        _write_json_private(self.manifest_path, entries)
        return dict(entry)
    # 把 Profile 的 CDP 地址登记到 dashboard_chromes.json，供本地监控页面发现并连接该 Chrome。
    # endpoint 形如 http://127.0.0.1:9222，仅监听/访问本机回环接口。
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
    # 根据清单记录启动或复用专用 Chrome。
    # 新启动时设置独立 user-data-dir、仅绑定 127.0.0.1 的 CDP 端口，并在配置存在时通过 --proxy-server 指定该 Profile 的本地代理。
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
            proxy_url = validate_profile_proxy(str(entry.get("proxy_url", "")))
            if proxy_url:
                args.append(f"--proxy-server={proxy_url}")
        args.append(url)
        subprocess.Popen(args, cwd=str(self.root))
        return "already_running" if already_running else "started"

# 在命令行打印 Profile 名称、CDP 端口、在线状态和用户数据目录，便于人工检查。
def _print_entries(manager: DedicatedChromeProfiles) -> None:
    entries = manager.entries()
    if not entries:
        print("尚未创建专用 Chrome Profile。")
        return
    print("\n名称\t端口\t状态\tProfile 目录")
    for item in entries:
        status = "在线" if endpoint_online(int(item["port"])) else "离线"
        print(f"{item['name']}\t{item['port']}\t{status}\t{item['profile_dir']}")

# 提供最小交互式菜单：创建并打开 Profile、打开已有 Profile，或仅列出当前 Profile。
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

# 定义非交互命令行接口，支持 create、start、list、interactive 四种子命令。
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

# 程序入口：解析命令行参数并分派到交互菜单、列表、创建或启动流程。
# 预期的输入/文件/系统错误统一打印到 stderr，并以退出码 2 表示失败。
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

# 仅在直接执行本文件时调用 main；作为模块导入时不会自动启动交互流程。
if __name__ == "__main__":
    raise SystemExit(main())
