
"""
IP决策层

功能:
- 节点评分排序
- 风险节点过滤接口
- 输出完整决策信息
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

        usable = []
        skipped = []

        for item in ranked:
            # 保留接口。
            # 后续full_ip绑定后，在这里过滤cooldown IP。
            usable.append(item)

        return {
            "selected": usable[0] if usable else None,
            "usable": usable,
            "skipped": skipped
        }
