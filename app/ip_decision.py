
"""
IP决策层

职责:
1. 根据历史池过滤不可用IP
2. 根据节点评分排序
3. 输出候选节点选择结果

不直接控制Clash。
"""

class IpDecisionEngine:

    def __init__(self, history, score_store):
        self.history = history
        self.score_store = score_store


    def rank_nodes(self, nodes):
        """
        nodes:
        [
          {
            "clash_name": "...",
            "type": "..."
          }
        ]
        """

        result = []

        for node in nodes:
            name = node["clash_name"]

            score = self.score_store.get_score(
                name
            )

            result.append({
                "node": node,
                "score": score.get(
                    "score",
                    0
                )
            })

        result.sort(
            key=lambda x:x["score"],
            reverse=True
        )

        return result


    def filter_cooling(self, candidates):

        usable = []
        skipped = []

        for item in candidates:

            node = item["node"]

            # 当前版本只检查已有IP记录
            # 完整IP绑定将在后续接入

            usable.append(item)

        return usable, skipped
