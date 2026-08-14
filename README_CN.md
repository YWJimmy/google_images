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
