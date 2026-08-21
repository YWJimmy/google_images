
"""
IP决策层

根据:
- 节点评分
- IP历史

生成候选顺序。
"""

class IpDecisionEngine:

    def __init__(self, history, score_store):
        self.history = history
        self.score_store = score_store


    def rank_nodes(self, nodes):

        ranked=[]

        for node in nodes:
            name=node["clash_name"]

            score=self.score_store.get_score(name)

            ranked.append({
                "node":node,
                "score":score.get("score",0)
            })

        ranked.sort(
            key=lambda x:x["score"],
            reverse=True
        )

        return ranked


    def choose(self,nodes):

        ranked=self.rank_nodes(nodes)

        return {
            "selected": ranked[0] if ranked else None,
            "usable": ranked,
            "skipped":[]
        }
