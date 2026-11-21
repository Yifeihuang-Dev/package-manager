"""biglib — a deliberately large demo package.

~20 MB of bundled data resources (words / sentences / numbers) plus small
query modules on top. Useful for testing install & streaming throughput.
"""

from pathlib import Path


def _read_data(name: str) -> str:
    """Read a bundled data file in any execution mode.

    vendor mode  -> real file next to this module
    cloud mode   -> importlib.resources (ResourceReader) -> cloud.resource
    """
    here = Path(__file__).parent / "data" / name
    try:
        return here.read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        from importlib.resources import files

        return files("biglib").joinpath("data", name).read_text(encoding="utf-8")
    except Exception:
        pass
    from package_manager import cloud

    return cloud.resource("biglib", f"data/{name}").decode("utf-8")


def info() -> dict:
    """Sizes of the bundled data files (bytes)."""
    import zipfile
    import io

    sizes = {}
    for name in ("words.txt", "sentences.json", "numbers.csv"):
        raw = _read_data(name)
        sizes[name] = len(raw.encode("utf-8"))
    return {"package": "biglib", "version": "1.0.0", "data_bytes": sizes,
            "data_total": sum(sizes.values())}


def word_count() -> int:
    from . import text

    return text.word_count()


def lookup(prefix: str, limit: int = 10) -> list:
    from . import text

    return text.lookup(prefix, limit)


def random_sentence() -> str:
    from . import text

    return text.random_sentence()


def number_stats() -> dict:
    from . import stats

    return stats.summary()
