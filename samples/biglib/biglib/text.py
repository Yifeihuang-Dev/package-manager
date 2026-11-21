"""Word/sentence queries over the bundled corpus."""

import functools
import json
import random

from . import _read_data


@functools.lru_cache(maxsize=1)
def _words() -> tuple:
    return tuple(_read_data("words.txt").split())


@functools.lru_cache(maxsize=1)
def _sentences() -> list:
    return json.loads(_read_data("sentences.json"))


def word_count() -> int:
    return len(_words())


def lookup(prefix: str, limit: int = 10) -> list:
    """First `limit` words starting with prefix (linear scan)."""
    return [w for w in _words() if w.startswith(prefix)][:limit]


def random_sentence() -> str:
    return random.choice(_sentences())


def longest_words(n: int = 5) -> list:
    return sorted(set(_words()), key=len, reverse=True)[:n]
