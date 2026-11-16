"""utils — 个人工具集。

门面：__init__.py 里 import 了什么，外部就能 `utils.xxx` 引用什么。
"""
from utils.retry import RetryError, retry
from utils.net.http import fetch

__all__ = ["retry", "RetryError", "fetch"]
