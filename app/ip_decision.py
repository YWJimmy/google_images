
"""
IP智能决策层

功能:
- 节点评分
- IP历史风险过滤
- challenge冷却过滤
- same egress风险过滤
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


    def _get_node_status(self, node):

        if not hasattr(self.history, "_load"):
            return None

        data=self.history._load()

        # 优先读取节点索引
        node_info=data.get("nodes",{}).get(node)

        if node_info and node_info.get("status"):
            return node_info.get("status")

        # 兼容旧数据: 从IP记录反查节点
        for _, item in data.get("ips",{}).items():

            if item.get("node")==node:
                return item.get("status")

        return None


    def choose(self, nodes):

        ranked=self.rank_nodes(nodes)

        usable=[]
        skipped=[]

        for item in ranked:

            node=item["node"]["clash_name"]

            status=self._get_node_status(node)

            if status=="challenge":
                skipped.append({
                    "node":node,
                    "reason":"ip_cooling"
                })
                continue

            if status=="same_egress":
                skipped.append({
                    "node":node,
                    "reason":"same_egress"
                })
                continue

            usable.append(item)

        return {
            "selected":usable[0] if usable else None,
            "usable":usable,
            "skipped":skipped
        }
