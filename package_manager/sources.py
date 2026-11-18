"""Remote package sources: cloud registries pkm can install from.

A source is any URL serving an index.json at its root with package files
under packages/<name>/<file> — a GitHub Pages site works as-is
(zero server cost). The index.json contract:

    {"packages": {"<name>": {"<version>": {
        "file": "<filename>", "sha256": "...", "pubkey": "...", "key_id": "..."
    }}}}
"""

import json
import urllib.request

from .config import SOURCES_FILE
from .errors import PkmError
from .util import load_json, save_json


def load_sources() -> dict:
    return load_json(SOURCES_FILE, {})


def add(name: str, url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise PkmError(f"source url must start with http:// or https://: {url}")
    url = url.rstrip("/")
    sources = load_sources()
    if name in sources and sources[name] != url:
        raise PkmError(f"source {name} already exists with a different url; remove it first")
    sources[name] = url
    save_json(SOURCES_FILE, sources)
    print(f"✓ source added: {name} -> {url}")


def remove(name: str) -> None:
    sources = load_sources()
    if name not in sources:
        raise PkmError(f"no such source: {name} (see pkm source list)")
    del sources[name]
    save_json(SOURCES_FILE, sources)
    print(f"✓ source removed: {name}")


def list_sources() -> None:
    sources = load_sources()
    if not sources:
        print("(empty) no remote sources; add one with: pkm source add <name> <url>")
        return
    for name, url in sorted(sources.items()):
        print(f"{name:<12} {url}")


def _ssl_context():
    """Build a working CA context: certifi -> macOS system bundle -> default.

    python.org builds of Python do not use the macOS keychain by default,
    which breaks HTTPS verification on a fresh install; this fixes that
    without any third-party dependency.
    """
    import ssl
    from pathlib import Path

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    mac_bundle = Path("/etc/ssl/cert.pem")
    if mac_bundle.exists():
        return ssl.create_default_context(cafile=str(mac_bundle))
    return None


def fetch(url: str) -> bytes:
    """Download bytes from a URL (30s timeout, pkm user agent)."""
    req = urllib.request.Request(url, headers={"User-Agent": "pkm/0.2"})
    try:
        ctx = _ssl_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.read()
    except Exception as e:
        raise PkmError(f"failed to fetch {url}: {e}")


def fetch_json(url: str) -> dict:
    try:
        return json.loads(fetch(url))
    except PkmError:
        raise
    except Exception as e:
        raise PkmError(f"invalid JSON from {url}: {e}")
