本次完整修复:

1. 修复 IpRotationService 未向 rotator 传递 decision_engine 的问题
2. 修复 rotate 返回 decision=None 的问题
3. 支持运行时注入决策引擎
4. 保留已有 Clash 多订阅/Selector 逻辑
5. 保留原错误记录方式，避免大范围破坏

下一阶段:
full_ip、IP历史绑定、Google challenge自动冷却。
