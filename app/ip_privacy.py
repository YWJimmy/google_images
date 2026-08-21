
"""
IP隐私显示工具

原则:
- 内部保存完整IP
- 日志默认脱敏
"""


def mask_ip(ip):

    if not ip:
        return None

    parts = ip.split(".")

    if len(parts) == 4:
        return ".".join(
            parts[:3]
        ) + ".xxx"

    return ip
