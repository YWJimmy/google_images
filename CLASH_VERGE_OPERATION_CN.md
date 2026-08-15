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

## 5. 为什么 IP 查询页与 Google 验证页显示不同

两个页面看到的 IP 不同，通常说明它们没有经过完全相同的网络路径，常见原因包括：

- Clash 处于规则模式，不同域名命中了不同规则或代理组；
- Google 验证页是在切换节点前生成的，页面仍显示之前请求的出口；
- 浏览器存在尚未关闭的旧连接，而新的 IP 查询使用了新连接；
- 代理服务商针对不同目标使用不同的 NAT 出口；
- 某些流量没有继承系统代理，直接经过本地网络；
- IPv4 与 IPv6 的路由方式不同。

排查顺序：

1. 保留验证页，不要继续自动请求。
2. 在同一浏览器、同一时间重新查询出口 IP。
3. 在 Clash Verge 的“连接”页面确认 Google 请求命中的规则、代理组和节点。
4. 关闭相关标签页后重新建立连接，再比较一次。
5. 若 Google 仍显示另一地址，检查是否存在 `DIRECT` 规则、绕过列表、其他 VPN/代理或 IPv6 直连。

IP 查询网站显示的经纬度不是第二个 IP。例如 `114.xx, 22.xx` 形式通常是位置数据库给出的经纬度。

## 6. 隐私与仓库规则

以下内容不得提交到 Git：

- Clash API 密钥；
- 订阅 URL、订阅文件和节点凭据；
- 完整公网 IP、节点服务器地址和账户标识；
- Clash 配置目录、日志原文及包含用户名的绝对路径；
- Cookie、浏览器 Profile、Storage State 或 CAPTCHA 页面截图。

代码若需要访问控制器，应从环境变量或交互式输入读取密钥，并在日志中对 IP、节点名和错误响应做脱敏。项目不得在检测到 Google 验证后自动切换节点继续运行。
