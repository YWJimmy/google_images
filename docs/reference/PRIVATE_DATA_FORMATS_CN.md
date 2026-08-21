# 私有数据格式与脱敏测试契约

本文公开项目私有数据的**结构**，用于编写测试、构造 fixture 和排查兼容问题。
示例全部为虚构值，不来自本机 `private/`、`profile/`、`logs/` 或真实数据库。

> 禁止把真实密钥、Cookie、完整公网 IP、Chrome Profile、搜索任务或运行数据库复制到
> issue、提交、测试 fixture 或聊天中。

## 1. 测试数据约定

- IPv4 使用 `192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24`。
- IPv6 使用 `2001:db8::/32`。
- 本机服务只使用 `127.0.0.1`、`localhost` 或 `::1`。
- 时间统一使用带时区的 ISO 8601，例如 `2026-08-22T10:30:00+00:00`。
- ID 可使用固定假值或 UUID hex；不要复用生产运行 ID。
- 单元测试应写入临时目录或 `private/test_<uuid>.*`，不能打开真实私有文件。

## 2. 文件清单与敏感等级

| 路径 | 格式 | 敏感等级 | 主要生产者 |
| --- | --- | --- | --- |
| `config.yaml` | YAML object | 本机配置 | 用户从 `config.example.yaml` 复制 |
| `keywords.local.csv` / `keywords.private.csv` | CSV | 搜索任务 | 用户 |
| `private/dashboard_secrets.json` | JSON object | 凭据 | `DashboardSecretStore` |
| `private/dashboard_chromes.json` | JSON array | 本机标识 | `DashboardManager` |
| `private/dedicated_chrome_profiles.json` | JSON array | 本机配置 | `DedicatedChromeProfiles` |
| `private/google_state.json` | Playwright storage state | 凭据 | `state_capture` |
| `private/ip_history.json` | JSON object | 完整 IP/节点 | `IpHistoryStore` |
| `private/node_score.json` | JSON object | 节点统计 | `NodeScoreStore` |
| `private/network_rotation_latest.json` | JSON object | 节点/脱敏出口 | `NetworkRotationTester` |
| `private/ip_intelligence.sqlite3` | SQLite | 完整 IP/节点历史 | `IpIntelligenceDatabase` |
| `logs/collection_telemetry.sqlite3` | SQLite | 运行元数据 | `CollectionTelemetry` |
| `rank_tracker.sqlite3` | SQLite | 关键词与结果 URL | `storage.open_db` |
| `profile/dedicated/<name>/` | Chrome user-data-dir | 凭据/浏览历史 | Chrome |
| `output/` | CSV | 关键词与排名结果 | `storage.export_csv` |
| `logs/diagnostics/` | JSON/PNG/TXT | 页面诊断 | `GoogleImagesBrowser` |

所有这些路径均被 `.gitignore` 排除。Chrome user-data-dir 是 Chrome 自有的多文件格式，
不应由测试手工构造；测试 Profile 管理时只需创建空目录并 mock Chrome/CDP。

## 3. JSON 格式

### 3.1 `dashboard_secrets.json`

```json
{
  "version": 1,
  "protection": "windows-dpapi-current-user",
  "clash_secret_dpapi": "BASE64_DPAPI_CIPHERTEXT_FOR_TEST_ONLY",
  "clash_endpoint": "http://127.0.0.1:9097",
  "clash_proxy_url": "http://127.0.0.1:7897"
}
```

`clash_secret_dpapi` 不是明文，也不是可跨用户使用的通用 Base64 密钥。测试必须 mock
`dpapi_protect`/`dpapi_unprotect`，不要把示例字符串交给 Windows DPAPI 解密。

### 3.2 `dashboard_chromes.json`

```json
[
  {
    "id": "chrome-9222",
    "label": "专用 Chrome test_01",
    "endpoint": "http://127.0.0.1:9222"
  }
]
```

`endpoint` 必须是带显式端口的本机 HTTP URL。

### 3.3 `dedicated_chrome_profiles.json`

```json
[
  {
    "name": "test_01",
    "port": 9222,
    "profile_dir": "profile/dedicated/test_01",
    "proxy_url": "http://127.0.0.1:7897",
    "proxy_status": "online",
    "masked_ip": "203.0.113.xxx",
    "proxy_latency_ms": 42,
    "proxy_checked_at": "2026-08-22T10:30:00+00:00",
    "restart_required": false
  }
]
```

必需字段是 `name`、`port`、`profile_dir`、`proxy_url`、`proxy_status`；其余代理检测字段
在首次检测前可以不存在。`proxy_status` 常见值为 `direct`、`unchecked`、`online`、
`unreachable`、`verification_seen`。

### 3.4 `google_state.json`

格式与 Playwright `storage_state()` 一致，项目只保留 Google 域的 Cookie 和 origin：

```json
{
  "cookies": [
    {
      "name": "TEST_COOKIE",
      "value": "DUMMY_NOT_A_REAL_SESSION",
      "domain": ".google.com",
      "path": "/",
      "expires": -1,
      "httpOnly": true,
      "secure": true,
      "sameSite": "Lax"
    }
  ],
  "origins": [
    {
      "origin": "https://images.google.com",
      "localStorage": [
        {"name": "test-key", "value": "dummy-value"}
      ]
    }
  ]
}
```

真实 Cookie/LocalStorage 等同登录凭据。测试过滤逻辑时使用上面的假值即可。

### 3.5 `ip_history.json`

```json
{
  "ips": {
    "203.0.113.10": {
      "node": "Test Node A",
      "group": "Proxy",
      "subnet": "203.0.113.0/24",
      "status": "same_egress",
      "cooling_until": "2026-08-22T11:00:00+00:00",
      "updated": "2026-08-22T10:30:00+00:00"
    }
  },
  "nodes": {
    "Test Node A": {
      "ip": "203.0.113.10",
      "group": "Proxy",
      "status": "same_egress",
      "cooling_until": "2026-08-22T11:00:00+00:00",
      "updated": "2026-08-22T10:30:00+00:00"
    }
  }
}
```

`status` 当前可能为 `ok`、`challenge`、`same_egress` 或读取时推导出的 `active`。
这是旧决策兼容存储；完整身份的主数据位于 IP Intelligence SQLite。

### 3.6 `node_score.json`

```json
{
  "Test Node A": {
    "success": 8,
    "fail": 2,
    "challenge": 1,
    "latencies": [41, 45, 39],
    "updated": "2026-08-22T10:30:00+00:00"
  }
}
```

计数和延时均为非负数；`latencies` 单位为毫秒。

### 3.7 `network_rotation_latest.json`

```json
{
  "mode": "pure_network_rotation",
  "tested_at": "2026-08-22T18:30:00+08:00",
  "group": "Proxy",
  "original_node": "Test Original",
  "original_restored": true,
  "services": ["cp.cloudflare.com/generate_204", "api.ipify.org"],
  "google_accessed": false,
  "requested_nodes": 1,
  "tested_nodes": 1,
  "usable_nodes": 1,
  "samples_per_node": 2,
  "results": [
    {
      "node": "Test Node A",
      "probe_delay_ms": 50,
      "requested_samples": 2,
      "successful_samples": 2,
      "failed_samples": 0,
      "masked_egresses": ["203.0.113.xxx"],
      "latency_ms": [40, 44],
      "status": "usable",
      "minimum_ms": 40,
      "median_ms": 42,
      "average_ms": 42,
      "maximum_ms": 44
    }
  ]
}
```

### 3.8 Clash 当前真实节点列表

当前本机 Clash Controller 的 `get_real_nodes_v21("XFLTD")` 直接列表输出已保存为
[`CLASH_REAL_NODE_LIST_CURRENT.json`](CLASH_REAL_NODE_LIST_CURRENT.json)。采集时 `GLOBAL`
和 `XFLTD` 得到相同的真实出口集合，因此只保留一份，避免重复。

该 JSON 保留 Controller 返回的原始 `clash_name` 和 `type`，用于节点名包含国旗 Emoji、
中文、空格及 `[D]`/`[V]` 后缀时的解析和决策测试。文件不包含 API 密钥、服务器地址、
端口、订阅 URL 或完整公网 IP。Clash 配置变化后应重新采集并整体替换，不手工改名。

### 3.9 Clash 当前 Selector 大类列表

[`CLASH_SELECTOR_LIST_CURRENT.json`](CLASH_SELECTOR_LIST_CURRENT.json) 保存当前
`controller.selectors()` 的直接输出，包含：

- `GLOBAL` 和 `XFLTD` 两个 Selector 大类；
- 每个大类的 `current`、最终落地节点 `current_leaf` 和完整 `choices`；
- `DIRECT`、`REJECT`、`XFLTD`、`自动选择`、`故障转移`等非真实出口选项；
- 所有 `[D]`/`[V]` 真实节点名。

该文件用于测试“大类/嵌套代理组不能被误当成真实出口节点”。它是运行状态快照，
`current` 和 `current_leaf` 会随 Clash 自动选择或人工切换而变化。

### 3.10 `all_proxies.json` 与 `clash_proxies.json`

这两份私有文件都是 Clash/Mihomo `/proxies` 接口的原始响应，顶层结构相同：

```json
{
  "proxies": {
    "代理或策略组名称": {
      "name": "代理或策略组名称",
      "type": "Selector | URLTest | Fallback | AnyTLS | Vless | ...",
      "alive": true
    }
  }
}
```

公开、可直接载入测试的脱敏样例分别是：

- [`ALL_PROXIES_FORMAT_EXAMPLE.json`](ALL_PROXIES_FORMAT_EXAMPLE.json)：对应私有
  `private/all_proxies.json`，保留 `GLOBAL`、业务 Selector、`自动选择`、`故障转移`、
  `AnyTLS` 节点，以及 `all`、`now`、`history`、`extra` 等原始层级；
- [`CLASH_PROXIES_FORMAT_EXAMPLE.json`](CLASH_PROXIES_FORMAT_EXAMPLE.json)：对应私有
  `private/clash_proxies.json`，保留 `GLOBAL`、`XFLTD`、`自动选择`、`故障转移`，并同时
  提供 `[D]`/`AnyTLS` 与 `[V]`/`Vless` 节点。

样例保持源文件的字段名称、值类型和嵌套关系，但仅选取能覆盖解析分支的代表对象。
UUID、实时测速时间、延迟、流量/到期提示和真实节点名称均替换为固定测试值；因此测试
不得把样例中的 `alive`、`now`、`delay` 当作当前 Clash 运行状态。

## 4. YAML 与 CSV 格式

### 4.1 `config.yaml`

字段以根目录 [`config.example.yaml`](../../config.example.yaml) 为唯一公开模板。测试应复制模板
到临时目录，并至少改写 `input_csv`、`database_path`、`output_dir`、`log_dir`、Profile 和
storage-state 路径，避免访问真实运行数据。它不应包含 Clash API 密钥；密钥使用 DPAPI store。

### 4.2 关键词输入 CSV

UTF-8/UTF-8-BOM，固定表头：

```csv
keyword,target_domain
Example Query,example.test
```

空行、空字段及重复的 `(keyword, target_domain)` 会被忽略。

### 4.3 排名导出 CSV

`storage.export_csv()` 的固定列顺序为：

```text
run_date,keyword,target_domain,result_code,result_type,matched_rank,matched_url,
collected_count,google_url,elapsed_ms,message,checked_at
```

诊断目录没有跨版本稳定的单一 schema；测试具体诊断功能时应以对应函数的返回字典为契约，
不要把真实截图、页面正文或 URL 当作 fixture。

## 5. SQLite 格式

SQLite 时间字段均为 ISO 8601 文本；布尔语义使用整数或状态文本。测试数据库应通过对应
Python 类初始化，让代码自动创建当前 schema，不建议复制空数据库二进制文件。

### 5.1 `ip_intelligence.sqlite3`

#### `node_table`

| 字段 | 类型/约束 | 含义 |
| --- | --- | --- |
| `node_id` | INTEGER PK | 节点内部编号 |
| `name` | TEXT UNIQUE NOT NULL | Clash 原始节点名 |
| `type` | TEXT NULL | 节点类型 |
| `country` | TEXT NULL | 国家/地区代码 |
| `first_seen` | TEXT NOT NULL | 首次发现时间 |
| `last_seen` | TEXT NOT NULL | 最近发现时间 |

#### `ip_identity_table`

| 字段 | 类型/约束 | 含义 |
| --- | --- | --- |
| `full_ip` | TEXT PK | 完整公网 IPv4/IPv6 |
| `country` | TEXT NULL | 国家/地区代码 |
| `first_seen`, `last_seen` | TEXT NOT NULL | 首次/最近观测时间 |
| `success`, `failure`, `challenge` | INTEGER NOT NULL | 信誉事件计数 |
| `status` | TEXT NOT NULL | `ACTIVE`、`COOLING`、`BLOCKED` |
| `cooling_until` | TEXT NULL | 冷却结束时间 |

#### `node_ip_history`

`id INTEGER PK`、`node TEXT`、`full_ip TEXT FK`、`observed_at TEXT`。每次观测追加一行，
用于计算 IP 变化率和复用率。

#### `ip_event`

`id INTEGER PK`、`full_ip TEXT NULL`、`node TEXT NULL`、`event_type TEXT`、
`occurred_at TEXT`、`details_json TEXT`。常见事件为 `success`、`failure`、`challenge`、
`same_egress`、`timeout`、`switch_failed`、`blocked`。

#### `rotation_log`

`id INTEGER PK`、`rotation_id TEXT`、`attempt_id INTEGER`、`node TEXT`、
`decision_node TEXT`、`full_ip TEXT NULL`、`result TEXT`、`state TEXT`、`reason TEXT NULL`、
`occurred_at TEXT`、`details_json TEXT`。`(rotation_id, attempt_id)` 唯一。

### 5.2 `collection_telemetry.sqlite3`

- `collection_runs`：`run_id`、模式、Chrome/会话模式、起止时间、请求/完成数量、
  搜索尝试数、Challenge 数与首个 Challenge 样本编号。
- `collection_events`：验证类型、样本/搜索/尝试编号、Profile/Chrome、脱敏 IP、代理状态、
  延时、Clash 组和节点。

该库不保存关键词、目标域名、完整 IP、Cookie、密钥或验证内容。

### 5.3 `rank_tracker.sqlite3`

- `results`：日期、关键词、目标域名、结果码、匹配排名/URL、采集量、Google URL、耗时和消息。
- `image_items`：结果外键、排名、来源页 URL 和图片 URL。

该库包含真实任务和 URL，应按私有数据处理。

## 6. 最小测试示例

```python
from pathlib import Path
import uuid

from app.database import IpIntelligenceDatabase
from app.ip_identity import IpIdentityService

path = Path("private") / f"test_ip_{uuid.uuid4().hex}.sqlite3"
database = IpIntelligenceDatabase(path)
identity = IpIdentityService(database)
identity.observe("Test Node A", "203.0.113.10", country="SG")

row = identity.for_node("Test Node A", reveal_full_ip=False)
assert row["masked_ip"] == "203.0.xxx.xxx"
assert "full_ip" not in row
```

运行完整验证：

```powershell
python -m pytest -q
python -m compileall -q app
```

字段的代码级事实来源为
[`app/database.py`](../../app/database.py)、
[`app/collection_telemetry.py`](../../app/collection_telemetry.py)、
[`app/storage.py`](../../app/storage.py) 以及各 JSON store 模块。
