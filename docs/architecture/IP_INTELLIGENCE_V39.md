# IP Intelligence Layer v3.9

本版本把 Clash 节点轮换升级为真实公网 IP 驱动的闭环：

```text
节点发现 → IP 决策 → 切换 → Full IP 验证 → 反馈/冷却 → fallback
```

## 核心行为

- 每次轮换生成唯一 `rotation_id`，每个候选生成递增 `attempt_id`。
- 状态机包含 `ROTATING`、`VERIFYING`、`COOLING`、`SUCCESS`、`FAILED`。
- 同一轮换实例拒绝并发切换并返回 `ROTATION_BUSY`。
- 每次切换均校验 `decision_selected == switch_target`；异常返回
  `DECISION_MISMATCH` 并立即停止。
- `same_egress`、切换失败或验证失败都会反馈给决策层，并自动选择下一个
  不同节点/不同已知出口池。
- 全部候选失败时尝试恢复轮换前节点。

## Full IP 身份

`FullIpCollector` 通过本机代理查询 ipify、ifconfig.me 和 ipinfo。多个服务均成功时
必须至少有两个结果一致；仅一个服务可用时允许降级运行。完整 IP 只写入：

```text
private/ip_intelligence.sqlite3
```

Dashboard 和 `/api/ip-intelligence` 只返回脱敏地址，不返回完整 IP。

控制台“检查脱敏出口”也使用同一套三服务共识采集器。若当前 Clash 组和最终叶子已知，
采集成功后立即追加 Node→Full IP 历史；响应只包含脱敏 IP、国家、采集时间、成功服务数
和共识票数。`same_egress` 仍是一次有效身份观测，因此同样写入映射和出口聚类。

SQLite 表：

- `node_table`：节点名称、类型和国家；
- `ip_identity_table`：IP 身份、信誉计数和冷却状态；
- `node_ip_history`：节点与 IP 的历史映射；
- `ip_event`：success、failure、challenge、same_egress 等事件；
- `rotation_log`：每次轮换 attempt 的执行结果。

## 决策与信誉

最终分数由节点质量、IP 信誉、IP 变化历史和成功率组成。`COOLING` 与 `BLOCKED`
IP 不会进入可用候选；同一已知出口集群在一次决策中只保留得分最高的节点。
Challenge 与 `same_egress` 都会使完整 IP 进入默认 30 分钟冷却。

## Dashboard 使用

1. 在“网络出口”中连接 Clash Controller。
2. 选择 Selector 代理组。
3. 点击“智能轮换”并确认。
4. 系统完成节点切换、完整 IP 验证、失败 fallback，并使用当前 Chrome 的 CDP
   网络路径进行二次出口核对。
5. 在“IP Intelligence”查看脱敏 IP、国家、状态、分数、Challenge 次数和同出口标记。

该功能不会自动处理或绕过 Google Challenge。检测到 Challenge 时只记录对应 IP、
进入冷却并等待人工处理。

## 验证

```powershell
python -m pytest -q
python -m compileall -q app
```
