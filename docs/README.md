# 项目文档索引

本文档目录按用途组织。除 `archive/` 外，其余文档均描述当前版本。

## 快速入口

- [项目总览与安装](../README_CN.md)
- [本地可视化控制台](guides/CONSOLE_GUIDE_CN.md)
- [Clash Verge、出口检查与智能轮换](guides/CLASH_VERGE_OPERATION_CN.md)
- [IP Intelligence Layer v3.9 架构](architecture/IP_INTELLIGENCE_V39.md)
- [私有数据格式与脱敏测试契约](reference/PRIVATE_DATA_FORMATS_CN.md)
- [当前 Clash 真实节点列表 JSON](reference/CLASH_REAL_NODE_LIST_CURRENT.json)
- [当前 Clash Selector 大类列表 JSON](reference/CLASH_SELECTOR_LIST_CURRENT.json)
- [`all_proxies.json` 脱敏格式样例](reference/ALL_PROXIES_FORMAT_EXAMPLE.json)
- [`clash_proxies.json` 脱敏格式样例](reference/CLASH_PROXIES_FORMAT_EXAMPLE.json)
- [Git 协作与提交](guides/git_guide.md)

## 目录约定

| 目录 | 内容 | 是否为当前规范 |
| --- | --- | --- |
| `guides/` | 安装后的操作方法和故障排查 | 是 |
| `architecture/` | 当前系统结构、状态机和设计边界 | 是 |
| `reference/` | 数据结构、接口约定和测试资料 | 是 |
| `archive/` | 历史版本说明、阶段性修复记录 | 否，仅用于追溯 |

## 文档维护规则

1. 根目录只保留主 README、示例配置、依赖文件和启动脚本。
2. 新功能应更新对应的当前指南或架构文档，不再新增零散的 `FIX_NOTE`。
3. 私有数据只能公开结构、字段和虚构示例；禁止复制真实文件内容。
4. 示例 IP 使用 RFC 5737/3849 文档地址，密钥、Cookie 和节点名称使用明显的假值。
5. 历史文档与当前实现冲突时，以当前代码、测试和非 `archive/` 文档为准。
