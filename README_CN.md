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

`homepage` 模式先访问 `https://images.google.com/ncr`。入口应落到 `images.google.com`；若仍落到 `images.google.com.hk` 等国家/地区域名，并且 `require_google_com_host: true`，程序会按导航错误停止。通过首页搜索框提交后，Google Images 结果页显示为 `www.google.com/search?...&udm=2` 是正常行为。

诊断 JSON 会记录 `images_home_url` 和 `images_home_landing_url`，用于确认 `/ncr` 是否实际生效。该设置只能固定入口域名，不能保证消除 Google challenge。

首页提交后程序默认等待 `results_load_wait_ms: 4000`，再开始检查结果 DOM。诊断 JSON 的 `result_dom_summary` 只记录链接数量分类，不保存具体结果网址或页面正文。

单图片结构探针只点击第一个可见的 Google Images 结果，用于判断页面产生预览面板、新标签页还是外部导航：

```powershell
python -m app.main --config config.yaml --probe-first-image --probe-keyword "Albert Einstein"
```

报告保存到 `logs/diagnostics/first_image_probe_*.json`。报告不记录搜索词、完整 URL、`goto` 令牌或 Cookie 值，只保留结构变化和规范化来源域名；探针每次只点击一张图片，遇到 challenge 或 consent 会停止。

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
