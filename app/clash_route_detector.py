"""
v3.2 Clash出口组自动发现
"""

class ClashRouteDetector:
    def __init__(self, controller, proxy_url=None):
        self.controller = controller
        self.proxy_url = proxy_url

    def discover_candidates(self):
        data = self.controller._request("/proxies")
        result = []

        for name, item in data.get("proxies", {}).items():
            if item.get("type") not in {
                "Selector", "URLTest",
                "Fallback", "LoadBalance"
            }:
                continue

            now = item.get("now", "")
            if now in {"", "DIRECT", "REJECT"}:
                continue

            result.append({
                "group": name,
                "type": item.get("type"),
                "current": now
            })

        return result

    def find_active_group(self):
        groups = self.discover_candidates()

        if not groups:
            return None

        priority = {
            "Selector": 0,
            "Fallback": 1,
            "URLTest": 2,
            "LoadBalance": 3,
        }

        groups.sort(
            key=lambda x: priority.get(x["type"], 99)
        )

        return groups[0]
