"""极简 JSON GET（主要用来演示 pkm 的多层目录结构）。"""
import json as _json
from urllib.request import urlopen


def fetch(url: str, timeout: int = 10):
    with urlopen(url, timeout=timeout) as resp:
        return _json.loads(resp.read().decode())
