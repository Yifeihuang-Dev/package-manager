"""Filesystem layout conventions for pkm."""

from pathlib import Path

PKM_HOME = Path.home() / ".pkm"
KEYS_DIR = PKM_HOME / "keys"
REGISTRY_DIR = PKM_HOME / "registry"
REGISTRY_PACKAGES = REGISTRY_DIR / "packages"
REGISTRY_INDEX = REGISTRY_DIR / "index.json"
KNOWN_KEYS_FILE = PKM_HOME / "known_keys.json"


def ensure_dirs() -> None:
    for d in (PKM_HOME, KEYS_DIR, REGISTRY_PACKAGES):
        d.mkdir(parents=True, exist_ok=True)
