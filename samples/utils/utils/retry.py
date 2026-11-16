"""简单的重试装饰器。"""
import functools
import time


class RetryError(Exception):
    pass


def retry(times=3, delay=0.5):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for _ in range(times):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    last = e
                    time.sleep(delay)
            raise RetryError(f"重试 {times} 次仍失败: {last}") from last

        return wrapper

    return deco
