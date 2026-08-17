from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import ipaddress
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen
# 只允许访问本机回环地址上的 Clash/Mihomo Controller 或代理端口，避免控制请求误发往远程主机。
# 127.0.0.1 和 ::1 分别是 IPv4/IPv6 回环地址；localhost 通常解析到其中之一。
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# 这些类型表示“代理组”而非最终出口节点，解析当前出口或枚举叶子节点时需要继续向下展开。
GROUP_PROXY_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay"}
# 这些类型不作为可测速的实际代理节点加入 leaf 列表，例如 Direct 表示直连、Reject 表示拒绝连接。
NON_TESTABLE_PROXY_TYPES = {"Direct", "Reject", "RejectDrop", "Pass", "Compatible"}
# 节点延迟检测使用返回 HTTP 204 的轻量 URL，Mihomo Controller 会通过指定节点发起测试。
DEFAULT_DELAY_TEST_URL = "https://cp.cloudflare.com/generate_204"


# 统一封装 Clash/Mihomo Controller 通信、返回格式或代理检测失败，便于上层集中处理。
class ClashControllerError(RuntimeError):
    pass

# 校验 Controller/代理 URL：必须是 http、主机必须为本机回环地址，并且必须显式提供端口。
def validate_local_http_url(value: str, name: str) -> str:
    url = value.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS or not parts.port:
        raise ValueError(f"{name} must be a local http URL with an explicit port")
    return url

# 校验 Clash External Controller 的 API secret，拒绝空值、异常超长值和控制字符。
def validate_secret(value: str) -> str:
    secret = value.strip()
    if not secret or len(secret) > 512 or any(ord(char) < 32 for char in secret):
        raise ValueError("Clash API secret is required")
    return secret


# 对公网出口 IP 做脱敏显示：IPv4 隐去最后一段；IPv6 只保留前四组。
# 该函数用于展示，不改变实际网络出口。
def mask_ip(value: str) -> str:
    address = ipaddress.ip_address(value.strip())
    if address.version == 4:
        parts = str(address).split(".")
        return ".".join([*parts[:3], "xxx"])
    parts = address.exploded.split(":")
    return ":".join(parts[:4]) + ":…"

# 把规范化 IP 做 SHA-256，并只保留前 16 个十六进制字符，得到用于“是否同一出口”比较的短指纹。
# 这样可以比较出口变化而不在状态数据中直接保存完整 IP。
def _ip_fingerprint(value: str) -> str:
    """Return a short non-reversible comparison token for an IP address."""
    normalized = str(ipaddress.ip_address(value.strip())).encode("ascii")
    return hashlib.sha256(normalized).hexdigest()[:16]


# Clash/Mihomo External Controller 的最小客户端：读取代理状态、切换 Selector、关闭连接、测速节点。
class ClashController:
    # 初始化时立即校验 Controller 地址和 API secret，后续所有 API 请求复用这两个值。
    def __init__(self, endpoint: str, secret: str):
        self.endpoint = validate_local_http_url(endpoint, "controller endpoint")
        self.secret = validate_secret(secret)
    # 统一发送 Controller HTTP 请求。payload 存在时编码为 JSON，并通过 Bearer Token 传递 API secret。
    # Controller 的 HTTP 错误与连接/超时/JSON 解析错误会转换为 ClashControllerError。
    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        request_timeout: float = 3,
    ) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.endpoint + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.secret}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=request_timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raise ClashControllerError(f"Clash controller returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise ClashControllerError("Clash controller connection failed") from None
    # 读取 /version 和当前 Selector 列表，返回供控制台展示的 Controller 在线状态摘要。
    def status(self) -> dict:
        version = self._request("/version")
        selectors = self.selectors()
        return {
            "online": True,
            "version": str(version.get("version", "unknown"))[:80],
            "selectors": selectors,
        }
    # 读取 /proxies，只提取 type=Selector 的代理组。
    # 同时返回当前直接选项 current，以及沿嵌套代理组继续解析得到的最终叶子节点 current_leaf。
    def selectors(self) -> list[dict]:
        state = self._request("/proxies")
        proxies = state.get("proxies", {})
        if not isinstance(proxies, dict):
            raise ClashControllerError("Clash controller returned an invalid proxy list")
        result = []
        for group_name, value in proxies.items():
            if not isinstance(value, dict) or value.get("type") != "Selector":
                continue
            choices = [str(item) for item in value.get("all", []) if isinstance(item, str)]
            current = str(value.get("now", ""))
            result.append(
                {
                    "group": str(group_name),
                    "current": current,
                    "current_leaf": self._resolve_current_leaf(proxies, current),
                    "choices": choices,
                }
            )
        return result
    @staticmethod
    # 沿代理组的 now 字段逐层向下解析最终节点；visited 用于防止异常配置形成循环引用后无限循环。
    def _resolve_current_leaf(proxies: dict, name: str) -> str:
        current = name
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            value = proxies.get(current)
            if not isinstance(value, dict) or str(value.get("type", "")) not in GROUP_PROXY_TYPES:
                return current
            next_name = value.get("now")
            if not isinstance(next_name, str) or not next_name:
                return current
            current = next_name
        return current
    # 切换某个 Selector 当前选择的节点。
    # 先确认代理组存在且目标节点确实属于该组，再向 /proxies/{group} 发送 PUT {'name': node}。
    def switch(self, group: str, node: str) -> dict:
        group_name = group.strip()
        node_name = node.strip()
        selectors = {item["group"]: item for item in self.selectors()}
        if group_name not in selectors:
            raise ValueError("unknown Selector group")
        if node_name not in selectors[group_name]["choices"]:
            raise ValueError("node is not a current choice of this Selector group")
        self._request(f"/proxies/{quote(group_name, safe='')}", "PUT", {"name": node_name})
        return {"ok": True, "group": group_name, "current": node_name}
    # 删除 Mihomo 当前活动连接，使后续新连接立即按刚切换的路由重新建立；它本身不负责选择节点。
    def close_connections(self) -> dict:
        """Close Mihomo's active connections so new requests use the selected route."""
        self._request("/connections", "DELETE")
        return {"ok": True}
    # 取得 /proxies 的完整代理字典，并过滤掉非字典项，供节点展开和测速流程复用。
    def _proxy_state(self) -> dict[str, dict]:
        state = self._request("/proxies")
        proxies = state.get("proxies", {})
        if not isinstance(proxies, dict):
            raise ClashControllerError("Clash controller returned an invalid proxy list")
        return {str(name): value for name, value in proxies.items() if isinstance(value, dict)}
    @staticmethod
    # 递归展开指定 Selector 下的嵌套代理组，收集真正可进行延迟测试的叶子代理节点。
    # visited 同样用于防止循环；Direct/Reject 等非测试类型不会进入结果。
    def _leaf_nodes(proxies: dict[str, dict], group: str) -> list[str]:
        if group not in proxies or str(proxies[group].get("type", "")) != "Selector":
            raise ValueError("unknown Selector group")
        leaves: list[str] = []
        visited: set[str] = set()
        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            value = proxies.get(name, {})
            proxy_type = str(value.get("type", ""))
            if proxy_type in GROUP_PROXY_TYPES:
                for child in value.get("all", []):
                    if isinstance(child, str):
                        visit(child)
                return
            if proxy_type and proxy_type not in NON_TESTABLE_PROXY_TYPES:
                leaves.append(name)
        for choice in proxies[group].get("all", []):
            if isinstance(choice, str):
                visit(choice)
        return leaves
    # 调用 Mihomo 的 /proxies/{node}/delay 接口测试单个节点到 DEFAULT_DELAY_TEST_URL 的延迟。
    # 返回值必须是合理的正整数毫秒数，否则视为测试失败。
    def _delay_for_node(self, name: str, timeout_ms: int) -> int:
        query = urlencode(
            {"url": DEFAULT_DELAY_TEST_URL, "timeout": timeout_ms, "expected": "204"}
        )
        response = self._request(
            f"/proxies/{quote(name, safe='')}/delay?{query}",
            request_timeout=(timeout_ms / 1000) + 2,
        )
        delay = response.get("delay")
        if not isinstance(delay, int) or delay <= 0 or delay >= 65535:
            raise ClashControllerError("proxy delay check failed")
        return delay
    # 批量测试一个 Selector 中所有可测试叶子节点，但不修改当前选中的节点。
    # 最多使用 8 个线程并发测速；结果按“可用优先、延迟升序、名称”排序。
    def probe_group_nodes(
        self, group: str, *, timeout_ms: int = 3000, max_nodes: int = 200
    ) -> dict:
        """Test a Selector's leaf nodes without changing the selected node."""
        if not 500 <= timeout_ms <= 10000:
            raise ValueError("timeout_ms must be between 500 and 10000")
        if not 1 <= max_nodes <= 500:
            raise ValueError("max_nodes must be between 1 and 500")
        proxies = self._proxy_state()
        names = self._leaf_nodes(proxies, group)
        truncated = len(names) > max_nodes
        names = names[:max_nodes]
        results: dict[str, dict] = {}
        workers = min(8, max(1, len(names)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._delay_for_node, name, timeout_ms): name for name in names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    delay = future.result()
                    results[name] = {
                        "name": name,
                        "type": str(proxies[name].get("type", "unknown"))[:40],
                        "usable": True,
                        "delay_ms": delay,
                        "status": "可用",
                    }
                except Exception:
                    results[name] = {
                        "name": name,
                        "type": str(proxies[name].get("type", "unknown"))[:40],
                        "usable": False,
                        "delay_ms": None,
                        "status": "超时或不可用",
                    }
        ordered = sorted(
            results.values(),
            key=lambda item: (not item["usable"], item["delay_ms"] or 10**9, item["name"]),
        )
        return {
            "group": group,
            "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "test_method": "mihomo_http_delay",
            "test_url": DEFAULT_DELAY_TEST_URL,
            "timeout_ms": timeout_ms,
            "tested": len(ordered),
            "usable": sum(1 for item in ordered if item["usable"]),
            "unusable": sum(1 for item in ordered if not item["usable"]),
            "truncated": truncated,
            "nodes": ordered,
        }

# 通过指定的本机 HTTP 代理访问 api.ipify.org，确认该代理实际看到的公网出口身份。
# 返回脱敏 IP、不可逆短指纹和检测耗时；完整 IP 不放入返回结构。
def proxy_egress_identity(proxy_url: str) -> dict:
    """Check proxy egress without returning or persisting the full IP address."""
    proxy = validate_local_http_url(proxy_url, "proxy URL")
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    request = Request("https://api.ipify.org", headers={"User-Agent": "local-network-check/1"})
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=6) as response:
            raw = response.read().decode("ascii").strip()
        address = ipaddress.ip_address(raw)
        return {
            "ok": True,
            "family": f"IPv{address.version}",
            "masked_ip": mask_ip(raw),
            "ip_fingerprint": _ip_fingerprint(raw),
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise ClashControllerError("proxy egress check failed") from None

# 在 proxy_egress_identity 基础上进一步移除 IP 指纹，只保留适合界面展示的脱敏出口信息。
def masked_proxy_egress(proxy_url: str) -> dict:
    sample = proxy_egress_identity(proxy_url)
    sample.pop("ip_fingerprint", None)
    return sample
