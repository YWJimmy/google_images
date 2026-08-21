
# IP统一改造说明

## 新架构

runner.py
    |
    v
ip_manager.py
    |
    v
current_chrome_ip.py
    |
    v
clash_controller.py
    |
    v
Clash API


## 设计目标

- 多代理组兼容
- 多订阅兼容
- 统一IP切换入口
- 统一错误返回
- 支持Google challenge自动恢复


## 后续接入

Google返回 -4:
GOOGLE_CHALLENGE_OR_UNUSUAL_TRAFFIC

流程:

1. runner记录当前IP
2. 调用IpRotationService.rotate()
3. Clash切换出口
4. 验证新IP
5. 重试任务
