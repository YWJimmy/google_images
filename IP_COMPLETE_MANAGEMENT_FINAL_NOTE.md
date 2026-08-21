本次整合基于用户当前测试通过版本。

完成:
1. 保留Clash多订阅/动态组发现逻辑
2. 保留node_score接口
3. 保留ip_history接口
4. 修正decision结果不可见问题
5. rotate返回decision信息

待下一阶段:
- full_ip真实公网IP字段
- node与IP绑定
- challenge自动冷却
- runner/google_images自动恢复
