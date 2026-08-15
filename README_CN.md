# Google Images 本地排名追踪器 v1

## 重要边界

本项目**不使用任何外部 SERP API**。它在你的本机启动 Google Chrome，通过 Playwright 打开 Google Images 搜索页面并解析结果。

同时，本项目**不会**：

- 破解或自动解决 CAPTCHA；
- 绕过 `unusual traffic`；
- 自动切换 IP/VPN/代理；
- 伪造 Cookie、Session 或浏览器指纹；
- 随机鼠标/键盘/停顿去伪装真人；
- 在出现 Google 反自动化挑战后继续强行请求。

一旦检测到 CAPTCHA / unusual traffic，返回 `-4` 并停止当天运行。

## 1. 默认任务

- 每天读取最多 1000 个关键词；
- 运行窗口 8 小时；
- 平均调度间隔 28.8 秒；
- 每个关键词尝试收集前 100 个 Google Images 来源页 URL；
- 找到目标域名：返回最靠前的 1~100；
- 完整前 100 中未找到：`-1`；
- 页面挑战：`-4`；
- 结果不足 100：默认 `-5`。

## 2. 错误码

| result_code | 含义 |
|---:|---|
| 1..100 | 目标域名最佳排名 |
| -1 | 完整前100中未找到目标域名 |
| -2 | 页面导航/网络错误 |
| -3 | Chrome/Profile 启动错误 |
| -4 | CAPTCHA / unusual traffic / automated queries 挑战 |
| -5 | 未收集到完整前100结果 |
| -6 | 结果解析错误（保留） |
| -7 | 输入数据错误 |
| -8 | 未分类内部错误 |
| -9 | Google consent 页面，需要人工处理 |
| -10 | 搜索导航与来源域名解析超过 5 秒处理预算 |

## 3. 安装要求

- Windows 10/11
- Python 3.11+
- 已安装 Google Chrome
- 本机能够正常手工访问 Google Images

## 4. Windows 安装

解压后双击：

```text
install_windows.bat
```

它会创建 `.venv` 并安装：

```text
playwright
PyYAML
```

本项目使用你本机已经安装的 Chrome (`channel: chrome`)，不下载单独 Chromium。

## 5. 配置检查

双击：

```text
validate_windows.bat
```

正常会显示：

```text
Configuration OK
daily_limit=1000
run_hours=8.0
interval_seconds=28.80
```

## 6. 第一次测试

**不要第一次就直接跑 1000 个。**

建议先编辑 `config.yaml`，临时改：

```yaml
daily_limit: 10
run_hours: 0.1
```

这样 10 个测试任务约分布在 6 分钟内。

然后运行：

```text
run_test_10_windows.bat
```

测试完成后恢复：

```yaml
daily_limit: 1000
run_hours: 8
```

## 7. 正式运行

双击：

```text
run_daily_windows.bat
```

程序只启动一个可见 Chrome 窗口，并使用：

```text
profile/google_profile
```

作为专用持久化 Profile。

请不要同时用另一个 Chrome 进程打开同一个 profile 目录，否则 Chrome 会因为 profile lock 无法启动。

## 8. 搜索方式

程序访问：

```text
https://www.google.com/search?q=<keyword>&udm=2&hl=en&gl=us
```

`udm=2` 为 Google Images 搜索界面。

为了收集最多 100 个来源页，程序只做固定滚动加载：

```yaml
max_scroll_rounds: 12
scroll_pixels: 1800
scroll_wait_ms: 1200
```

这些是页面加载参数，不是“模拟真人”的随机行为参数。

## 9. 结果解析

Google 的前端 DOM 可能变化，因此 v1 避免绑定易变 CSS class。

解析逻辑：

1. 获取当前 DOM 中所有 `a[href]`；
2. 优先解析 Google 图片链接中的：
   - `imgrefurl`
   - `imgurl`
   - Google redirect 参数 `url` / `q`
3. 也接受直接外链；
4. 排除 google.com / gstatic / googleusercontent 等内部域名；
5. 按 DOM 顺序去重；
6. 前 100 个外部来源页作为本次候选图片结果。

注意：Google 页面结构不是公开稳定 API，因此**本地直抓版本的排名准确性必须通过你的实际样本人工抽查验证**。如果 Google 改 DOM，需要更新解析器。

## 10. 输入文件

`keywords_1000.csv`：

```csv
keyword,target_domain
Albert Einstein,wikipedia.org
Marie Curie,wikipedia.org
Eiffel Tower,wikipedia.org
```

已经内置 1000 行示例数据，目标域名统一使用 `wikipedia.org`。

正式使用时直接替换即可。

### 子域名

默认：

```yaml
include_subdomains: true
```

因此：

```text
en.wikipedia.org
zh.wikipedia.org
```

都匹配：

```text
wikipedia.org
```

## 11. 输出

### 主数据库

```text
rank_tracker.sqlite3
```

表：

```text
results
image_items
```

`image_items` 会保存本次收集到的每个来源页 URL 及其顺序。

### 每日 CSV

```text
output/results_YYYY-MM-DD.csv
```

关键字段：

```text
keyword
target_domain
result_code
result_type
matched_rank
matched_url
collected_count
google_url
elapsed_ms
message
checked_at
```

### 日志

```text
logs/run_YYYY-MM-DD.log
```

## 12. CAPTCHA / unusual traffic

程序会检查：

```text
/sorry/
Our systems have detected unusual traffic
unusual traffic from your computer network
I'm not a robot
reCAPTCHA
automated queries
```

若命中：

```text
result_code = -4
```

随后停止当天任务，不自动刷新、不换 IP、不自动处理验证码。

## 13. 断点恢复

每个关键词完成后立即写入 SQLite 和 CSV。

当日重新运行时，会跳过已经完成的 `(keyword, target_domain)`。

## 14. Windows 每日自动启动

可以用 Windows“任务计划程序”每天固定时间执行：

```text
C:\你的目录\google_images_local_v1\run_daily_windows.bat
```

例如 09:00 启动，则程序按 8 小时窗口调度 1000 个任务。

## 15. 现实限制

这是“网页自动化”而不是官方 API，所以需要接受这些事实：

- Google 页面 DOM 可能改变；
- 排名会受地区、语言、登录/未登录、个性化等因素影响；
- Google 可能限制自动查询；
- 1000 次/日是否能完整跑完不能靠代码保证；
- `-5` 比错误地返回 `-1` 更安全，因为未收集完整前100时不能证明目标域名不存在。

因此第一轮重点应是：

1. 用 10~50 个关键词验证解析结果与人工看到的排名是否一致；
2. 再逐步扩大任务数量；
3. 一旦出现挑战，让程序停下来，而不是继续强行请求。

---

## v1.1：关于“第一个关键词立即 -4”的修复

v1 的挑战检测包含过宽的字符串 `recaptcha`。正常 Google 页面如果包含非挑战用途的 reCAPTCHA 文本/标记，也可能被误判为 `-4`。

v1.1 改为只认以下明确证据：

- 当前 URL 进入 `/sorry/`；
- 页面存在**可见** reCAPTCHA iframe；
- 页面可见正文包含明确的 `unusual traffic` / `automated queries` 挑战语句。

同时新增：

- `-9 GOOGLE_CONSENT_REQUIRED`：Google 普通 consent 页面，不再误记成 CAPTCHA；
- `logs/diagnostics/`：遇到 `-4` 或 `-9` 时自动保存截图与可见正文，便于判断实际页面；
- 日志会直接打印触发原因；
- `bootstrap_profile_windows.bat`：打开程序使用的**同一个专用 Chrome Profile**，可手工完成普通 consent 设置后关闭。它不会处理或绕过 CAPTCHA。

若 v1.1 再出现 `-4`，请先打开 `logs/diagnostics/challenge_*.png`。若截图确实是 Google unusual-traffic/reCAPTCHA 页面，则该结果不是误报，程序会停止，不尝试绕过。

### v1.1 断点/错误重试修正

- 当天已经得到 `1..100` 或 `-1` 的任务视为最终完成，再次运行会跳过；
- `-2..-9` 等运行错误仍可在修复问题后再次运行并重试；
- 重启时调度索引按“当前剩余任务”重新计算，不再因为前面已完成任务的原始序号而额外等待数小时。

因此，如果 v1 产生了 `Albert Einstein = -4`，使用 v1.1 后**不需要手工删除 SQLite 记录**；该负错误会自动成为可重试任务。

---

## v1.2：浏览器环境诊断模式

当日常 Chrome 正常、程序专用 Profile 却进入 `/sorry/` 时，运行：

```powershell
python -m app.main --config config.yaml --diagnose
```

Windows 也可以双击：

```text
diagnose_windows.bat
```

默认使用公开测试关键词 `Albert Einstein`。如需指定其他诊断关键词：

```powershell
python -m app.main --config config.yaml --diagnose --diagnose-keyword "Albert Einstein"
```

诊断模式只访问一次 Google Images 搜索页并读取环境，不点击、不刷新、不处理验证码，也不修改浏览器指纹。报告保存在：

```text
logs/diagnostics/environment_YYYYMMDD_HHMMSS.json
logs/diagnostics/environment_YYYYMMDD_HHMMSS.png
logs/diagnostics/environment_YYYYMMDD_HHMMSS.txt
```

报告包括：

- Chrome 与 Playwright 版本；
- 配置的 Profile 路径及 Chrome 实际报告的 Profile 路径；
- Chrome 实际命令行和本项目显式添加的启动参数；
- 当前 URL、页面标题、User-Agent 与 navigator 基本信息；
- challenge / consent 判断及理由；
- Google Cookie 数量、LocalStorage origin/条目数量；
- 根据常见 Google 登录 Cookie **名称**推断的登录提示。

如果 Chrome 在页面打开前就退出，命令会以退出码 `2` 结束，并生成 `launch_failure_*.json`。该报告记录启动错误、沙箱配置以及可能存在的 Profile 锁文件；锁文件只是线索，不代表一定有 Chrome 进程占用。请先关闭使用程序专用 Profile 的全部 Chrome 窗口后重试，不要直接删除仍被使用的 Profile 数据。

诊断命令提供适合脚本判断的进程退出码：

| 退出码 | 含义 |
| ---: | --- |
| `0` | 页面状态正常 |
| `2` | 网络或导航错误 |
| `3` | Chrome / Profile 启动错误 |
| `4` | Google challenge / unusual traffic |
| `9` | Google consent 页面 |

JSON 同时包含 `result_code`、`result_type` 和 `process_exit_code`。为避免诊断报告携带可复用的临时信息，`/sorry/` URL 中的挑战令牌会被替换为 `<redacted>`。

---

## v1.3：导入人工 Chrome 状态

该模式只复用人工 Chrome 保存的 Cookie 和 LocalStorage，不复制日常 Chrome Profile，不自动处理 CAPTCHA，也不保证 Google 不再触发验证。

### 1. 打开独立的人工 Chrome

双击：

```text
start_manual_state_chrome_windows.bat
```

脚本使用独立目录 `profile/manual_state_capture/` 和本地 CDP 端口 `9222`。请在这个窗口中人工打开 Google Images、处理普通 consent，并确认普通搜索可用。不要把日常 Chrome 的 User Data 路径传给脚本。

如果页面是 `/sorry/`、reCAPTCHA 或 unusual traffic，请停止，不要保存当前状态。

### 2. 保存人工状态

保持上述 Chrome 窗口打开，双击：

```text
capture_google_state_windows.bat
```

也可以运行：

```powershell
python -m app.main --config config.yaml --capture-state --cdp-endpoint http://127.0.0.1:9222
```

捕获器会检查 Google 页面状态。发现 challenge 或 consent 时退出且不写文件；正常时保存到：

```text
private/google_state.json
```

控制台只显示 Cookie 和 origin 的数量，不输出 Cookie 名称、值、具体页面 URL 或搜索词。

### 3. 切换会话模式

确认状态文件生成后，在本地 `config.yaml` 中设置：

```yaml
session_mode: "storage_state"
storage_state_path: "private/google_state.json"
persist_storage_state_updates: false
images_home_url: "https://images.google.com/ncr"
search_navigation: "homepage"
require_google_com_host: true
```

`false` 表示自动搜索结束时不覆盖人工快照，避免把 challenge 状态写回。需要继续使用原有完整专用 Profile 时改回：

```yaml
session_mode: "persistent_profile"
```

如需让自动搜索、Chrome 历史记录和人工验证都发生在当前可见的专用窗口中，先运行 `start_manual_state_chrome_windows.bat` 并保持窗口打开，再设置：

```yaml
session_mode: "manual_cdp"
cdp_endpoint: "http://127.0.0.1:9222"
```

`manual_cdp` 会直接复用该 Chrome 的现有 context 和 Google 标签页。程序结束时只断开调试连接，不关闭窗口；如果检测到 challenge，也会停止并把页面留在窗口中供人工处理。该模式不会读取、复制或提交 Chrome History 文件，但通过同一窗口产生的正常导航会由 Chrome 自身记录。

`homepage` 模式先访问 `https://images.google.com/ncr`。入口应落到 `images.google.com`；若仍落到 `images.google.com.hk` 等国家/地区域名，并且 `require_google_com_host: true`，程序会按导航错误停止。通过首页搜索框提交后，Google Images 结果页显示为 `www.google.com/search?...&udm=2` 是正常行为。

诊断 JSON 会记录 `images_home_url` 和 `images_home_landing_url`，用于确认 `/ncr` 是否实际生效。该设置只能固定入口域名，不能保证消除 Google challenge。

`results_load_wait_ms: 4000` 是结果候选的最大自适应等待时间，不再是固定暂停。程序每隔 `results_poll_interval_ms: 100` 检查一次 Top-N 图片锚点，数量达到要求并连续稳定后立即解析。`search_parse_timeout_ms: 5000` 只约束搜索导航与域名解析，不计入该自适应等待；解析阶段使用剩余预算，超时任务标记为 `SEARCH_PARSE_TIMEOUT` 并继续后续任务。正式任务与诊断测试共用图片结果卡片结构化解析器，不再扫描页面全部外链，也不会按重复来源 URL 压缩排名。

正式排名采用失败关闭规则：目标命中只有在它之前的所有图片位置均已解析时才返回精确名次；只有前 Top-N 全部存在且来源域名全部解析时才能返回 `-1`。结果深度不足或任一必要位置为 `ambiguous/missing` 时返回 `-5`。challenge 检测始终立即停止整个运行，不提供关闭开关。

单图片结构探针只点击第一个可见的 Google Images 结果，用于判断页面产生预览面板、新标签页还是外部导航：

```powershell
python -m app.main --config config.yaml --probe-first-image --probe-keyword "Albert Einstein"
```

报告保存到 `logs/diagnostics/first_image_probe_*.json`。报告不记录搜索词、完整 URL、`goto` 令牌或 Cookie 值，只保留结构变化和规范化来源域名；探针每次只点击一张图片，遇到 challenge 或 consent 会停止。

基于搜索页最小结构化元数据块执行 10 个样例的 Top 100 有界测试：

```powershell
python -m app.main --config config.yaml --source-domain-test --limit 10 --test-max-results 100 --test-time-budget-seconds 300
```

来源测试默认在每次搜索完全结束后固定等待 `6` 秒，再启动下一次搜索；成功、失败和 5 秒超时跳过都遵循该规则。可用 `--test-post-search-delay-seconds` 显式覆盖，报告中的 `previous_finish_to_start_ms` 可用于核验。

## 本地可视化监测页面

监控页面同时也是统一操作控制台，用于替代日常双击多个 `.bat`。页面包含：

- 配置校验；
- 从当前专用 Chrome 捕获人工状态；
- 环境诊断与单图片结构探针；
- Top-N 来源域名测试与正式排名任务；
- 安装或修复 Python 依赖（执行前必须再次确认）；
- 创建、启动和监听多个隔离的专用 Chrome Profile；
- 实时查看脱敏后的命令输出、退出码和开始/结束时间。
- 连接本机 Clash Verge 控制器，读取 Selector 代理组、人工选择节点，并检查脱敏出口 IP。

所有后台操作都由固定白名单映射为参数数组，不接受任意 shell 命令。一个 CDP 端点正在运行可视化排名任务时，不允许再对同一端点启动诊断、探针或其他控制任务。

Clash API 密钥只保留在当前页面输入框，并随单次本机请求发送到 `127.0.0.1` 控制器；服务端不保存密钥、完整公网 IP 或节点信息。网络控制器不会订阅 challenge 事件，也不会自动切换节点。Google 出现验证时仍暂停，由用户人工检查、决定是否手动切换并完成验证。

### 创建多个专用 Chrome Profile

双击：

```text
dedicated_chrome_tool_windows.bat
```

工具支持创建并打开新 Profile、重新打开已有 Profile，以及列出每个 Profile 的调试端口和在线状态。创建时输入一个仅包含字母、数字、下划线或连字符的名称；端口可直接接受工具建议值。Chrome 会以可见窗口打开，之后由用户手动访问网页。

每个 Profile 保存于 `profile/dedicated/<名称>/`，本地登记信息保存于 `private/dedicated_chrome_profiles.json`，并自动加入 `private/dashboard_chromes.json`。这些路径均已被 `.gitignore` 排除。工具不会复制日常 Chrome Profile，不读取或导出 Cookie、History，也不会自动处理人机验证。如果监控页面已在运行，创建新 Profile 后需重启监控服务，或在页面中手动添加对应 CDP 地址。

也可使用命令行：

```powershell
python -m app.chrome_profile_tool create --name manual_02 --port 9224 --url https://images.google.com/ncr
python -m app.chrome_profile_tool start --name manual_02
python -m app.chrome_profile_tool list
```

调试端口仅绑定 `127.0.0.1`，用于监控页面或程序连接该专用窗口。使用期间不要运行来源不明的本机程序，也不要把端口转发到局域网或公网。

保持一个或多个使用独立 profile 和调试端口启动的 Chrome 窗口打开，然后双击：

```text
start_dashboard_windows.bat
```

也可以运行：

```powershell
python -m app.dashboard --config config.yaml --open-browser
```

页面仅监听 `http://127.0.0.1:8765/`，提供以下功能：

- 自定义登记多个本机 CDP 地址，例如 `http://127.0.0.1:9222`、`http://127.0.0.1:9223`；
- 每个 Chrome 窗口独立显示在线、当前页面、任务进度和关键词状态，并可同时运行；
- challenge 或 consent 出现后暂停当前关键词，每 10 秒检查一次可见页面；人工处理完成后自动重试同一关键词；
- 每次关键词结束（包括 5 秒超时）后固定等待 6 秒再继续；
- 用户可输入一个完整 `http(s)` URL，在选定窗口中正常打开，由 Chrome 自身写入 History。

窗口列表保存在被忽略的 `private/dashboard_chromes.json`。仪表盘和 CDP 端点都只允许本机回环地址；页面不显示 Cookie、challenge token 或完整搜索结果 URL。不要同时让两个任务控制同一个 CDP 端点。

测试按样例顺序串行加载页面，但不会点击图片。图片锚点直接提供外部 `href` 时优先读取其规范化域名；页面只提供 Google `goto` 令牌时，再从页面 HTML、内嵌脚本和单结果 DOM 祖先中寻找包含该令牌的最小结构块。仅当结构块中存在唯一外部域名时才标记为已解析。总预算会按剩余样例动态分配。报告保存到 `logs/diagnostics/source_domain_test_*.json`，不记录搜索词、完整 URL 或 `goto` 令牌。存在缺失、多域名歧义或匹配项之前仍有未解析位置时，只标记为不完整，不会误报精确排名或目标不存在。

### 隐私边界

`private/google_state.json` 包含可复用的浏览器会话信息，应视为凭据。以下路径已经被 `.gitignore` 排除：

```text
private/
google_state.json
*.storage-state.json
*.storage_state.json
profile/
```

提交前仍应运行：

```powershell
git check-ignore -v private\google_state.json profile\manual_state_capture
git status --ignored --short
```

不要通过聊天、邮件或公开仓库分享状态 JSON。若状态文件意外泄露，应删除文件、退出相关 Google 会话，并在 Google 账号中检查活动会话。

为保护隐私，报告不会保存 Cookie 值。登录提示只是诊断线索，不是对实际登录状态的证明。`logs/` 已被 `.gitignore` 排除，不应提交诊断报告；分享报告前仍应检查其中的本机 Profile 路径、Chrome 命令行和页面内容。
