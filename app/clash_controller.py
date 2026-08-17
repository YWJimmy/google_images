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
    url = value.strip().rstrip("/")  # 规范化 Controller/代理 URL，去掉首尾空白和末尾斜杠。
    parts = urlsplit(url)  # 拆出协议、主机和端口，用结构化方式做安全校验。
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS or not parts.port:  # 只接受带显式端口的本机 HTTP 地址，例如 http://127.0.0.1:9090。
        raise ValueError(f"{name} must be a local http URL with an explicit port")
    return url

# 校验 Clash External Controller 的 API secret，拒绝空值、异常超长值和控制字符。
def validate_secret(value: str) -> str:
    secret = value.strip()  # 去掉 API secret 首尾空白。
    if not secret or len(secret) > 512 or any(ord(char) < 32 for char in secret):  # 拒绝空值、异常超长值和控制字符。
        raise ValueError("Clash API secret is required")
    return secret


# 对公网出口 IP 做脱敏显示：IPv4 隐去最后一段；IPv6 只保留前四组。
# 该函数用于展示，不改变实际网络出口。
def mask_ip(value: str) -> str:
    address = ipaddress.ip_address(value.strip())  # 严格解析 IPv4/IPv6 地址，非法文本会直接报错。
    if address.version == 4:
        parts = str(address).split(".")  # IPv4 拆成四段，后面只隐藏最后一段。
        return ".".join([*parts[:3], "xxx"])  # 例如 203.0.113.25 显示为 203.0.113.xxx。
    parts = address.exploded.split(":")  # IPv6 先展开为固定八组，再保留前四组用于脱敏显示。
    return ":".join(parts[:4]) + ":…"

# 把规范化 IP 做 SHA-256，并只保留前 16 个十六进制字符，得到用于“是否同一出口”比较的短指纹。
# 这样可以比较出口变化而不在状态数据中直接保存完整 IP。
def _ip_fingerprint(value: str) -> str:
    """Return a short non-reversible comparison token for an IP address."""
    normalized = str(ipaddress.ip_address(value.strip())).encode("ascii")  # 先规范化 IP 写法，确保同一个 IPv6 的不同文本形式得到相同指纹。
    return hashlib.sha256(normalized).hexdigest()[:16]  # 用 SHA-256 的前 16 个十六进制字符做短指纹，只用于比较出口是否相同。


# Clash/Mihomo External Controller 的最小客户端：读取代理状态、切换 Selector、关闭连接、测速节点。
class ClashController:
    # 初始化时立即校验 Controller 地址和 API secret，后续所有 API 请求复用这两个值。
    def __init__(self, endpoint: str, secret: str):
        self.endpoint = validate_local_http_url(endpoint, "controller endpoint")  # External Controller 常见形式为 http://127.0.0.1:9090。
        self.secret = validate_secret(secret)  # 这是 Controller API 的 Bearer Token，不是代理节点密码。
    # 统一发送 Controller HTTP 请求。payload 存在时编码为 JSON，并通过 Bearer Token 传递 API secret。
    # Controller 的 HTTP 错误与连接/超时/JSON 解析错误会转换为 ClashControllerError。
    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        request_timeout: float = 3,
    ) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None  # 有 payload 时序列化为 UTF-8 JSON；普通 GET 可不带请求体。
        request = Request(  # 构造 urllib 请求对象，真正发送发生在后面的 urlopen()。
            self.endpoint + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.secret}",  # 使用 Authorization: Bearer <secret> 对 External Controller API 鉴权。
                "Content-Type": "application/json",  # 声明请求体使用 JSON 格式。
            },
        )
        try:
            with urlopen(request, timeout=request_timeout) as response:  # 向已限制为本机的 Controller 发请求，并设置超时避免永久阻塞。
                raw = response.read().decode("utf-8")  # 读取 Controller 返回字节并按 UTF-8 解码。
                return json.loads(raw) if raw else {}  # 有响应体时解析 JSON；空响应体返回空字典。
        except HTTPError as exc:  # Controller 已响应但返回 4xx/5xx 时进入这里。
            raise ClashControllerError(f"Clash controller returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):  # 连接失败、超时、系统网络错误或 JSON 异常统一转换成项目异常。
            raise ClashControllerError("Clash controller connection failed") from None
    # 读取 /version 和当前 Selector 列表，返回供控制台展示的 Controller 在线状态摘要。
    def status(self) -> dict:
        version = self._request("/version")  # 读取 Mihomo/Clash 内核版本，同时验证 Controller 是否可访问。
        selectors = self.selectors()  # 进一步读取 Selector 状态，因此 status() 会同时验证 /version 与 /proxies。
        return {
            "online": True,
            "version": str(version.get("version", "unknown"))[:80],
            "selectors": selectors,
        }
    # 读取 /proxies，只提取 type=Selector 的代理组。
    # 同时返回当前直接选项 current，以及沿嵌套代理组继续解析得到的最终叶子节点 current_leaf。
    def selectors(self) -> list[dict]:
        state = self._request("/proxies")  # /proxies 是节点枚举、当前选择和后续切换的核心状态接口。
        proxies = state.get("proxies", {})  # 提取 API 返回中的 proxies 字典；缺失时先使用空字典。
        if not isinstance(proxies, dict):
            raise ClashControllerError("Clash controller returned an invalid proxy list")
        result = []
        for group_name, value in proxies.items():  # 遍历全部代理项，但这里只收集 type=Selector 的代理组。
            if not isinstance(value, dict) or value.get("type") != "Selector":  # 普通节点或其他代理组类型在 selectors() 中跳过。
                continue
            choices = [str(item) for item in value.get("all", []) if isinstance(item, str)]  # all 是该 Selector 当前允许选择的直接子项名称列表。
            current = str(value.get("now", ""))  # now 表示 Selector 当前直接选择项，它本身可能仍是另一个代理组。
            result.append(
                {
                    "group": str(group_name),
                    "current": current,
                    "current_leaf": self._resolve_current_leaf(proxies, current),  # 继续沿嵌套代理组解析，得到最终实际出口叶子节点。
                    "choices": choices,
                }
            )
        return result
    @staticmethod
    # 沿代理组的 now 字段逐层向下解析最终节点；visited 用于防止异常配置形成循环引用后无限循环。
    def _resolve_current_leaf(proxies: dict, name: str) -> str:
        current = name  # 从 Selector 当前直接选择项开始向下解析。
        visited: set[str] = set()  # 记录已访问名称，防止错误配置形成 A→B→A 的无限循环。
        while current and current not in visited:  # 只要当前名称非空且未访问过，就继续解析下一层。
            visited.add(current)  # 把当前节点加入防循环集合。
            value = proxies.get(current)  # 从 /proxies 的完整状态中取当前节点或代理组详情。
            if not isinstance(value, dict) or str(value.get("type", "")) not in GROUP_PROXY_TYPES:  # 不是代理组时说明已经到达叶子节点，可直接返回。
                return current
            next_name = value.get("now")  # 代理组的 now 指向当前实际使用的下一层节点/子组。
            if not isinstance(next_name, str) or not next_name:
                return current
            current = next_name  # 把解析游标移动到下一层继续处理。
        return current
    # 切换某个 Selector 当前选择的节点。
    # 先确认代理组存在且目标节点确实属于该组，再向 /proxies/{group} 发送 PUT {'name': node}。
    def switch(self, group: str, node: str) -> dict:
        group_name = group.strip()  # 去掉 Selector 名称首尾空白。
        node_name = node.strip()  # 去掉目标节点名称首尾空白。
        selectors = {item["group"]: item for item in self.selectors()}  # 转成 group→状态字典，便于快速检查目标 Selector。
        if group_name not in selectors:  # 拒绝切换不存在的 Selector。
            raise ValueError("unknown Selector group")
        if node_name not in selectors[group_name]["choices"]:  # 目标必须是该 Selector 当前 all 列表中的合法直接选项。
            raise ValueError("node is not a current choice of this Selector group")
        self._request(f"/proxies/{quote(group_name, safe='')}", "PUT", {"name": node_name})  # PUT /proxies/{group} 并提交 {'name': 节点} 完成 Selector 切换；组名会做 URL 编码。
        return {"ok": True, "group": group_name, "current": node_name}  # 表示 Controller 接受了切换请求，不等价于新节点公网连通性已经验证。
    # 删除 Mihomo 当前活动连接，使后续新连接立即按刚切换的路由重新建立；它本身不负责选择节点。
    def close_connections(self) -> dict:
        """Close Mihomo's active connections so new requests use the selected route."""
        self._request("/connections", "DELETE")  # 关闭 Mihomo 当前活动连接，使新连接按刚选择的路由重新建立。
        return {"ok": True}
    # 取得 /proxies 的完整代理字典，并过滤掉非字典项，供节点展开和测速流程复用。
    def _proxy_state(self) -> dict[str, dict]:
        state = self._request("/proxies")  # /proxies 是节点枚举、当前选择和后续切换的核心状态接口。
        proxies = state.get("proxies", {})  # 提取 API 返回中的 proxies 字典；缺失时先使用空字典。
        if not isinstance(proxies, dict):
            raise ClashControllerError("Clash controller returned an invalid proxy list")
        return {str(name): value for name, value in proxies.items() if isinstance(value, dict)}  # 过滤结构异常的条目，并统一把代理名称转换为字符串。
    @staticmethod
    # 递归展开指定 Selector 下的嵌套代理组，收集真正可进行延迟测试的叶子代理节点。
    # visited 同样用于防止循环；Direct/Reject 等非测试类型不会进入结果。
    def _leaf_nodes(proxies: dict[str, dict], group: str) -> list[str]:
        if group not in proxies or str(proxies[group].get("type", "")) != "Selector":  # 批量测速入口必须是真实存在的 Selector。
            raise ValueError("unknown Selector group")
        leaves: list[str] = []  # 保存递归展开后真正可测试的叶子代理节点。
        visited: set[str] = set()  # 记录已访问名称，防止错误配置形成 A→B→A 的无限循环。
        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            value = proxies.get(name, {})  # 读取当前名称对应的代理详情。
            proxy_type = str(value.get("type", ""))  # type 用于区分代理组、真实代理节点以及 Direct/Reject 等特殊动作。
            if proxy_type in GROUP_PROXY_TYPES:  # 遇到代理组时继续展开 all 子项，而不是把组本身当成出口节点。
                for child in value.get("all", []):  # 递归遍历当前代理组的全部子项。
                    if isinstance(child, str):
                        visit(child)
                return
            if proxy_type and proxy_type not in NON_TESTABLE_PROXY_TYPES:  # 只把真实、可测速的代理叶子节点加入结果。
                leaves.append(name)  # 记录一个实际可测速节点。
        for choice in proxies[group].get("all", []):
            if isinstance(choice, str):
                visit(choice)
        return leaves
    # 调用 Mihomo 的 /proxies/{node}/delay 接口测试单个节点到 DEFAULT_DELAY_TEST_URL 的延迟。
    # 返回值必须是合理的正整数毫秒数，否则视为测试失败。
    def _delay_for_node(self, name: str, timeout_ms: int) -> int:
        query = urlencode(  # 把测速 URL、超时和期望状态码编码成查询参数。
            {"url": DEFAULT_DELAY_TEST_URL, "timeout": timeout_ms, "expected": "204"}
        )
        response = self._request(  # 调用 Mihomo delay API，由内核通过指定节点执行测试。
            f"/proxies/{quote(name, safe='')}/delay?{query}",  # 节点名做完整 URL 编码，支持中文、空格等名称。
            request_timeout=(timeout_ms / 1000) + 2,  # Controller 请求超时比节点测速超时多留 2 秒。
        )
        delay = response.get("delay")  # 正常返回值是毫秒延迟。
        if not isinstance(delay, int) or delay <= 0 or delay >= 65535:  # 过滤缺失、非整数、0/负值和 65535 一类无效延迟。
            raise ClashControllerError("proxy delay check failed")
        return delay
    # 批量测试一个 Selector 中所有可测试叶子节点，但不修改当前选中的节点。
    # 最多使用 8 个线程并发测速；结果按“可用优先、延迟升序、名称”排序。
    def probe_group_nodes(
        self, group: str, *, timeout_ms: int = 3000, max_nodes: int = 200
    ) -> dict:
        """Test a Selector's leaf nodes without changing the selected node."""
        if not 500 <= timeout_ms <= 10000:  # 单节点测速限制为 0.5–10 秒。
            raise ValueError("timeout_ms must be between 500 and 10000")
        if not 1 <= max_nodes <= 500:  # 一次最多允许处理 500 个节点。
            raise ValueError("max_nodes must be between 1 and 500")
        proxies = self._proxy_state()  # 先获取实时代理拓扑。
        names = self._leaf_nodes(proxies, group)  # 把指定 Selector 展开成实际叶子节点列表。
        truncated = len(names) > max_nodes  # 记录是否因为 max_nodes 限制而截断。
        names = names[:max_nodes]  # 在线程池创建前硬性截断工作量。
        results: dict[str, dict] = {}  # 按节点名保存每个测速任务的最终结果。
        workers = min(8, max(1, len(names)))  # 最多 8 个并发线程；即使节点为空也保证 max_workers 至少为 1。
        with ThreadPoolExecutor(max_workers=workers) as pool:  # 建立线程池并发执行多个节点的独立 delay 测试。
            futures = {pool.submit(self._delay_for_node, name, timeout_ms): name for name in names}  # 保存 Future→节点名映射，完成时可以知道结果属于哪个节点。
            for future in as_completed(futures):  # 按任务实际完成顺序处理，不必等待慢节点之后再读取快节点结果。
                name = futures[future]  # 由 Future 反查对应节点名称。
                try:
                    delay = future.result()  # 取得线程返回延迟；线程内异常会在此重新抛出。
                    results[name] = {
                        "name": name,
                        "type": str(proxies[name].get("type", "unknown"))[:40],
                        "usable": True,  # 成功得到有效延迟时标记节点可用。
                        "delay_ms": delay,
                        "status": "可用",
                    }
                except Exception:
                    results[name] = {
                        "name": name,
                        "type": str(proxies[name].get("type", "unknown"))[:40],
                        "usable": False,  # 测速异常或超时时只标记当前节点不可用，不终止整组任务。
                        "delay_ms": None,
                        "status": "超时或不可用",
                    }
        ordered = sorted(  # 所有测试结束后统一排序结果。
            results.values(),
            key=lambda item: (not item["usable"], item["delay_ms"] or 10**9, item["name"]),  # 排序顺序：可用优先 → 延迟低优先 → 名称稳定排序。
        )
        return {
            "group": group,
            "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),  # 记录本轮节点检测完成时的本地时间及时区偏移。
            "test_method": "mihomo_http_delay",
            "test_url": DEFAULT_DELAY_TEST_URL,
            "timeout_ms": timeout_ms,
            "tested": len(ordered),
            "usable": sum(1 for item in ordered if item["usable"]),  # 汇总本轮可用节点数量。
            "unusable": sum(1 for item in ordered if not item["usable"]),  # 汇总本轮不可用节点数量。
            "truncated": truncated,
            "nodes": ordered,
        }

# 通过指定的本机 HTTP 代理访问 api.ipify.org，确认该代理实际看到的公网出口身份。
# 返回脱敏 IP、不可逆短指纹和检测耗时；完整 IP 不放入返回结构。
def proxy_egress_identity(proxy_url: str) -> dict:
    """Check proxy egress without returning or persisting the full IP address."""
    proxy = validate_local_http_url(proxy_url, "proxy URL")  # 出口检测同样只允许连接本机代理端口。
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))  # 为本次检测创建 opener，并让 HTTP/HTTPS 都通过指定本机代理。
    request = Request("https://api.ipify.org", headers={"User-Agent": "local-network-check/1"})  # ipify 返回它看到的公网出口 IP，用于验证该代理真实出口。
    started = time.perf_counter()  # 使用高精度单调时钟记录检测开始时间。
    try:
        with opener.open(request, timeout=6) as response:  # 通过指定代理访问 ipify，并设置 6 秒超时。
            raw = response.read().decode("ascii").strip()  # 读取纯 IP 文本并去除换行。
        address = ipaddress.ip_address(raw)  # 严格确认远端返回内容确实是合法 IPv4/IPv6，而不是 HTML 错误页。
        return {
            "ok": True,
            "family": f"IPv{address.version}",  # 记录出口属于 IPv4 还是 IPv6。
            "masked_ip": mask_ip(raw),  # 返回脱敏 IP，不直接暴露完整出口地址。
            "ip_fingerprint": _ip_fingerprint(raw),  # 返回短指纹，供程序判断出口是否发生变化。
            "latency_ms": round((time.perf_counter() - started) * 1000),  # 计算完整出口检测耗时并转换为毫秒。
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):  # HTTP、代理连接、超时、系统网络或非法 IP 均视为出口检测失败。
        raise ClashControllerError("proxy egress check failed") from None

# 在 proxy_egress_identity 基础上进一步移除 IP 指纹，只保留适合界面展示的脱敏出口信息。
def masked_proxy_egress(proxy_url: str) -> dict:
    sample = proxy_egress_identity(proxy_url)  # 先执行完整出口身份检测。
    sample.pop("ip_fingerprint", None)  # 展示版本主动删除 IP 指纹，只保留脱敏信息。
    return sample  # 返回适合界面展示的代理出口状态。
