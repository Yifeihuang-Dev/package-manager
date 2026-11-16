"""logkit — 极简日志，跨包引用 vendor 进来的 utils。"""
import datetime

from utils.retry import retry


@retry(times=2, delay=0.1)
def log(msg: str) -> None:
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}")
