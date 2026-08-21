IP Decision Filter Fix

修复:
1. 兼容ips和nodes两种历史数据结构
2. challenge状态真正进入节点选择
3. same_egress进入跳过逻辑
4. record_ip同步保存node status

测试目标:
challenge节点应出现在 skipped.reason=ip_cooling
