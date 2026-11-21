"""Streaming numeric summary over the bundled numbers.csv."""

import functools

from . import _read_data


@functools.lru_cache(maxsize=1)
def summary() -> dict:
    """Single-pass stats over ~300k CSV rows (constant memory)."""
    count = 0
    total = 0
    lo = None
    hi = None
    for line in _read_data("numbers.csv").splitlines()[1:]:
        _, value, _sq = line.split(",")
        v = int(value)
        count += 1
        total += v
        if lo is None or v < lo:
            lo = v
        if hi is None or v > hi:
            hi = v
    return {
        "rows": count,
        "mean": round(total / count, 3),
        "min": lo,
        "max": hi,
        "sum": total,
    }
