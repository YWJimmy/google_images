# IP Decision Engine Binding Fix

修复:
- IpManager与IpRotator使用不同decision_engine实例的问题
- 保证IP历史状态实时进入rotate决策
- 保留Clash切换逻辑

验证目标:
challenge节点不能被rotate选择。
