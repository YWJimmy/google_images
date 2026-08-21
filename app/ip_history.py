
"""
IP历史池

保存:
- 出口IP
- 节点关系
- challenge冷却
"""

from pathlib import Path
import json
from datetime import datetime, timezone, timedelta


class IpHistoryStore:

    def __init__(self,path="private/ip_history.json"):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if not self.path.exists():
            self._save({"ips":{},"nodes":{}})


    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"ips":{},"nodes":{}}


    def _save(self,data):
        self.path.write_text(
            json.dumps(data,ensure_ascii=False,indent=2),
            encoding="utf-8"
        )


    def record_ip(self, ip, node=None, group=None,
                  status="ok", subnet=None, cooling_until=None):

        data=self._load()

        data["ips"][ip]={
            "node":node,
            "group":group,
            "subnet":subnet,
            "status":status,
            "cooling_until":cooling_until,
            "updated":datetime.now(timezone.utc).isoformat()
        }

        if node:
            data["nodes"][node]={
                "ip":ip,
                "group":group,
                "status":status,
                "cooling_until":cooling_until,
                "updated":datetime.now(timezone.utc).isoformat()
            }

        self._save(data)


    def get_ip(self,ip):
        return self._load()["ips"].get(ip)


    def is_cooling(self,ip,minutes=30):

        item=self.get_ip(ip)

        if not item:
            return False

        if item.get("status")!="challenge":
            return False

        updated=datetime.fromisoformat(item["updated"])

        return datetime.now(timezone.utc)-updated < timedelta(minutes=minutes)


    def mark_challenge(self,ip,node=None,group=None):
        self.record_ip(
            ip,
            node=node,
            group=group,
            status="challenge"
        )


    def mark_same_egress(self, ip, node=None, group=None, minutes=30):
        """
        记录出口重复风险。
        用于后续决策层降低节点优先级。
        """
        data = self._load()
        cooling_until = (
            datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ).isoformat()

        data["ips"][ip] = {
            "node": node,
            "group": group,
            "status": "same_egress",
            "cooling_until": cooling_until,
            "updated": datetime.now(timezone.utc).isoformat()
        }

        if node:
            data["nodes"][node] = {
                "ip": ip,
                "group": group,
                "status": "same_egress",
                "cooling_until": cooling_until,
                "updated": datetime.now(timezone.utc).isoformat()
            }

        self._save(data)


    def get_node_status(self, node):
        item = self._load().get("nodes", {}).get(node)
        if not item:
            return None
        status = item.get("status")
        cooling_until = item.get("cooling_until")
        if cooling_until:
            try:
                if datetime.fromisoformat(cooling_until) <= datetime.now(timezone.utc):
                    return "active"
            except ValueError:
                pass
        return status
