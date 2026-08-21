
"""
v3.7 节点评分模块

用于根据历史表现选择更稳定节点。
"""
import json
from pathlib import Path
from datetime import datetime, timezone


class NodeScoreStore:
    def __init__(self, path="private/node_scores.json"):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self,data):
        self.path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

    def record(self,node,success=True,challenge=False,latency_ms=None):
        data=self._load()
        item=data.setdefault(node,{
            "success":0,"fail":0,"challenge":0,
            "latency_total":0,"latency_count":0
        })
        if challenge:
            item["challenge"]+=1
        elif success:
            item["success"]+=1
        else:
            item["fail"]+=1
        if latency_ms:
            item["latency_total"]+=latency_ms
            item["latency_count"]+=1
        self._save(data)

    def score(self,node):
        item=self._load().get(node,{})
        success=item.get("success",0)
        fail=item.get("fail",0)
        challenge=item.get("challenge",0)
        total=success+fail+challenge
        rate=(success/total) if total else 0
        risk=(challenge/total) if total else 0
        avg=(item.get("latency_total",0)/item.get("latency_count",1))
        return round(rate*40 + (1-risk)*30 + max(0,20-avg/100) + (10 if challenge==0 else 0),2)
