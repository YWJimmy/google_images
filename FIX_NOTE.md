
本次修复目标:

1. 修复IP决策层存在但未进入执行链的问题
2. 增加choose接口，为ip_rotator接入准备
3. 保留旧Clash切换逻辑，避免破坏已验证功能

发现漏洞:
- node_score可以排序，但rotate仍随机选择节点
- ip_history尚未绑定full_ip，无法真实过滤冷却IP

下一步:
将choose结果接入ip_rotator。
