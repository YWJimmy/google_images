
"""
IP决策执行辅助层

负责:
- 节点排序
- 冷却节点过滤
- 选择最佳候选

不负责:
- Clash API调用
"""

class IpDecisionEngine:

    def __init__(self, history, score_store):
        self.history = history
        self.score_store = score_store


    def rank_nodes(self, nodes):
        ranked = []

        for node in nodes:
            name = node["clash_name"]

            score = self.score_store.get_score(name)

            ranked.append({
                "node": node,
                "score": score.get("score", 0)
            })

        ranked.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        return ranked


    def choose(self, nodes):
        ranked = self.rank_nodes(nodes)

        skipped = []
        usable = []

        for item in ranked:
            # 预留完整IP冷却检查
            # 当前需要由ip_history绑定full_ip后启用
            usable.append(item)

        return {
            "selected": usable[0] if usable else None,
            "usable": usable,
            "skipped": skipped
        }
