# Clash Verge 本地代理检查与手动切换操作

本文记录本项目在 Windows 上使用 Clash Verge 时的检查方法。内容仅用于确认流量出口、诊断网络路径和人工选择代理节点。

> 边界：Google 出现 CAPTCHA、`unusual traffic` 或其他验证页面后，应停止当天任务并由人工检查。不要通过自动换 IP、循环换节点或重放请求来规避验证。

## 1. 当前使用方式

- Clash Verge 开启“系统代理”。
- 本机混合代理监听地址为 `127.0.0.1:7897`。
- TUN 模式不是必需项；只使用系统代理时，应确认目标浏览器继承了系统代理。
- 外部控制器仅监听本机回环地址 `127.0.0.1:9097`，不得监听局域网地址或 `0.0.0.0`。
- API 访问密钥必须使用随机生成的强密钥，不要使用示例值，也不要提交到 Git。

## 2. 开启外部控制器

在 Clash Verge 的 Mihomo/外部控制器设置中：

1. 开启“外部控制器”。
2. 监听地址填写 `127.0.0.1:9097`。
3. 设置一个随机强密钥。
4. 点击“保存”。
5. 如端口未开始监听，重启 Mihomo 内核或退出后重新打开 Clash Verge。

不要在截图、日志、Issue、聊天记录或代码中公开真实密钥。

## 3. 只读验证

### 验证代理出口

以下命令只查询经代理访问时看到的公网出口：

```powershell
curl.exe --proxy http://127.0.0.1:7897 http://api.ipify.org
```

输出属于隐私信息。排查问题时只记录“成功/失败”或脱敏后的形式，例如 `203.0.113.xxx`。

### 验证控制器

将密钥临时放入当前 PowerShell 会话，不要写入仓库文件：

```powershell
$ClashApiSecret = Read-Host "Clash API secret"
$Headers = @{ Authorization = "Bearer $ClashApiSecret" }
Invoke-RestMethod -Uri "http://127.0.0.1:9097/version" -Headers $Headers
```

成功时会返回 Mihomo 版本。若提示“连接被拒绝”，依次检查：

1. 是否点击了“保存”；
2. 是否重启了 Mihomo 内核；
3. 监听地址是否仍为 `127.0.0.1:9097`；
4. 端口是否被其他程序占用。

## 4. 手动切换节点

先读取代理组，找到类型为 `Selector` 的业务代理组及其候选项：

```powershell
$State = Invoke-RestMethod -Uri "http://127.0.0.1:9097/proxies" -Headers $Headers
$State.proxies.PSObject.Properties |
    Where-Object { $_.Value.type -eq "Selector" } |
    ForEach-Object {
        [PSCustomObject]@{
            Group = $_.Name
            Current = $_.Value.now
            Choices = $_.Value.all.Count
        }
    }
```

确认组名和目标节点名后，人工执行一次切换：

```powershell
$GroupName = Read-Host "Proxy group"
$NodeName = Read-Host "Target node"
$EncodedGroup = [uri]::EscapeDataString($GroupName)
$Body = @{ name = $NodeName } | ConvertTo-Json
Invoke-RestMethod `
    -Method Put `
    -Uri "http://127.0.0.1:9097/proxies/$EncodedGroup" `
    -Headers $Headers `
    -ContentType "application/json" `
    -Body $Body
```

切换后重新查询出口 IP，并确认目标网站可以正常访问。若连通性异常，应手工切回原节点。

## 5. 节点可用性与延迟采集

控制台“可用节点延迟采集”会读取当前 Selector 组，并递归展开其中的实际代理节点。每个节点通过 Mihomo 的 `/proxies/{name}/delay` 接口访问固定的 `generate_204` 探测地址：

- 不修改当前 Selector 选择；
- 不访问 Google 搜索或图片页面；
- 使用最多 8 个并发探测，单节点超时限制为 500–10000 毫秒；
- 结果仅保留在当前页面响应中，展示节点名、协议类型、可用状态和 HTTP 往返延迟；
- 不返回节点服务器地址、订阅信息、凭据或完整出口 IP。

这里的延迟不是 ICMP `ping`。代理服务器经常禁用 ICMP，而 HTTP 探测更接近浏览器实际经过该代理建立连接的效果。出口 IP 仍需使用“检查脱敏出口”单独检查，并且只显示脱敏值。

测速完成后，节点下拉框和结果表会显示最近延迟。新产生的 challenge/consent 记录还会保存当时控制台所选的 Clash 组与节点，并在下次测速时显示“上次触发验证”时间。升级前的历史记录没有节点字段，控制台会显示为“无记录”，不会根据时间或 IP 猜测归属。

## 6. 多 Chrome 独立出口

当前只配置一个本机混合代理端口时，所有使用该端口的 Chrome 共享同一套路由与 Selector 当前节点，不能保证不同出口。Mihomo 支持配置多个 `listeners`；每个回环地址上的 mixed listener 可以通过 `proxy:` 固定到不同代理节点或策略组。随后给不同专用 Chrome 分别配置对应的 `http://127.0.0.1:<端口>`，即可建立独立映射。

这需要在 Clash Verge/Mihomo 配置层新增 listener 与独立策略组，当前控制台不会自动改写 Clash 配置。listener 应只监听 `127.0.0.1`，并在修改配置前备份和校验端口占用。

## 7. 自动轮换纯网络测试

下面的独立命令只访问 Cloudflare 204 探测地址和 ipify 出口查询，不访问 Google。它先按 Mihomo 延迟选出可直接切换的节点，再逐一切换；每个节点默认采样 10 次，完成或异常退出时都会在 `finally` 中恢复原 Selector：

```powershell
.\.venv\Scripts\python.exe -m app.network_rotation_test --nodes 5 --samples 10
```

可用 `--group "组名"` 指定 Selector；省略时选择候选节点最多的组。默认结果写入 Git 已忽略的 `private/network_rotation_latest.json`，其中只包含节点名、脱敏出口、延迟和成功/失败统计。`--output` 也被强制限制在 `private/` 内，防止误写入可提交目录。

## 8. 隐私与仓库规则

以下内容不得提交到 Git：

- Clash API 密钥；
- 订阅 URL、订阅文件和节点凭据；
- 完整公网 IP、节点服务器地址和账户标识；
- Clash 配置目录、日志原文及包含用户名的绝对路径；
- Cookie、浏览器 Profile、Storage State 或 CAPTCHA 页面截图。

代码若需要访问控制器，应从环境变量或交互式输入读取密钥，并在日志中对 IP、节点名和错误响应做脱敏。项目不得在检测到 Google 验证后自动切换节点继续运行。
