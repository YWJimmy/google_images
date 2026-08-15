# 本地可视化控制台说明

## 1. 启动与访问

在项目根目录双击：

```text
start_dashboard_windows.bat
```

或在 PowerShell 中运行：

```powershell
.\.venv\Scripts\python.exe -m app.dashboard --config config.yaml --open-browser
```

默认地址为 `http://127.0.0.1:8765/`。控制台与 Chrome 调试端口都仅监听本机回环地址，不应暴露到局域网或公网。

## 2. 页面区域

- **状态**：显示当前专用 Chrome 是否在线、任务状态、页面状态和排名任务进度。
- **专用 Chrome**：创建隔离 Profile，打开已有窗口，或监听另一个本机 CDP 端点。
- **网络出口**：连接本机 Clash Verge 控制器、读取代理组、人工切换节点及查看脱敏出口。
- **操作中心**：运行环境校验、诊断、单图探针、来源域名样本测试、日常任务和依赖安装。
- **排名任务**：在选中的专用 Chrome 中运行批量查询；遇到人工验证时暂停并轮询页面状态。

操作中心会显示：

- 当前动作与运行状态；
- 已用时间和最近一次输出时间；
- 可计算任务的 `当前项 / 总项数` 进度条；
- 子进程的实时输出；
- 成功、失败或停止结果。

## 3. 一键创建专用 Chrome

通常只需点击“**一键创建并打开**”。页面会自动补全：

- Profile 名称：依次使用 `manual_01`、`manual_02`；
- 调试端口：从 `9222` 起寻找未占用且未登记的本机端口；
- 起始网址：`https://images.google.com/ncr`。

需要自定义时展开“高级选项”。每个 Profile 的 Cookie、History、缓存等都只留在自己的目录，不复制日常 Chrome 数据。

## 4. 密钥与隐私数据保存位置

| 数据 | 本地位置 | 保护方式 |
|---|---|---|
| Clash API 密钥及控制器设置 | `private/dashboard_secrets.json` | 密钥由 Windows DPAPI 按当前用户加密；整个 `private/` 被 Git 忽略 |
| 专用 Chrome 数据 | `profile/dedicated/<Profile 名称>/` | 目录被 Git 忽略；含 Cookie、History、缓存等敏感数据 |
| Profile 登记表 | `private/dedicated_chrome_profiles.json` | 被 Git 忽略 |
| 控制台 Chrome 列表 | `private/dashboard_chromes.json` | 被 Git 忽略 |
| 捕获的 Google 会话状态 | `private/google_state.json` | 被 Git 忽略，应视作凭据 |
| 诊断日志 | `logs/diagnostics/` | `logs/` 被 Git 忽略；仍不应对外发送原始日志 |
| 运行与验证统计 | `logs/collection_telemetry.sqlite3` | 只记录序号、时间和状态，不记录关键词、域名、URL、Cookie 或密钥；`logs/` 被 Git 忽略 |
| 排名数据库 | `rank_tracker.sqlite3` | `*.sqlite3` 被 Git 忽略 |

控制台不会把密钥显示回页面。保存成功后，输入框可以留空；本机服务会在请求 Clash 控制器时临时解密。该密文通常只有同一台 Windows 上的同一用户可以解密。重装系统、切换用户或迁移文件后可能无法恢复，请保留密钥的独立安全备份。

如果还没有本地密钥文件，在“网络出口”区域输入一次 Clash API 密钥并点击“**本机加密保存**”，程序会自动创建它。点击“**清除已保存**”可移除其中的密钥字段。

可用以下命令检查隐私路径是否被 Git 忽略：

```powershell
git check-ignore -v private\dashboard_secrets.json
git check-ignore -v private\google_state.json
git check-ignore -v profile\dedicated\manual_01\Default\Cookies
git check-ignore -v rank_tracker.sqlite3
```

## 5. 常用流程

### 首次使用

1. 启动控制台。
2. 点击“一键创建并打开”。
3. 在打开的专用 Chrome 中手动完成网站访问、同意页面或必要设置。
4. 回到控制台，确认窗口显示“在线”。
5. 先运行“环境与配置校验”，再运行小样本测试。

### 人工验证

任务发现 Google 验证页面后会暂停，每 10 秒检查一次。请在同一个可见专用 Chrome 窗口中手动完成验证；页面恢复为普通 Google 页面后任务继续。控制台不会自动解答验证，也不会因验证自动切换 IP。

### 多窗口

每个专用 Chrome 必须使用不同 Profile 目录和调试端口。通过顶部“当前窗口”选择任务目标；不要让两个任务同时控制同一个端点。

## 6. 故障排查

- **页面仍显示旧功能**：停止旧控制台进程，重新运行启动脚本，然后强制刷新浏览器页面。
- **Chrome 显示离线**：确认窗口没有关闭，并检查其调试端口是否与控制台登记一致。
- **操作中心没有输出**：查看动作是否已进入运行状态；子进程启动后，最近输出时间和终端内容应持续更新。
- **密钥无法解密**：确认当前 Windows 用户与保存密钥时一致；清除后重新输入并保存。
- **Clash 连接失败**：确认外部控制器和密钥已在 Clash Verge 中启用，地址仅使用 `127.0.0.1` 或 `localhost`。
- **任务停止在验证页**：先确认验证已在受控的同一个 Chrome 标签页完成；必要时打开一个正常 Google 图片页，再等待下一次 10 秒轮询。

更详细的 Clash 人工操作边界见 `CLASH_VERGE_OPERATION_CN.md`，Git 流程见 `git_guide.md`。
