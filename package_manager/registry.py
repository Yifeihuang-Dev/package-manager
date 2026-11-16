"""Local registry: package store + index.json + optional git sync.

The registry is just a directory (~/.pkm/registry). Publishing auto-runs git init/commit;
once a remote is configured, `pkm registry push/pull` syncs it across machines.
"""

import shutil
import subprocess
from pathlib import Path

from . import pkging, signing
from .config import REGISTRY_DIR, REGISTRY_INDEX, REGISTRY_PACKAGES
from .errors import PkmError
from .manifest import parse_manifest, parse_version
from .util import load_json, now, save_json


def load_index() -> dict:
    return load_json(REGISTRY_INDEX, {"packages": {}})


def save_index(idx: dict) -> None:
    save_json(REGISTRY_INDEX, idx)


def publish(pkm_path: Path) -> None:
    REGISTRY_PACKAGES.mkdir(parents=True, exist_ok=True)
    pkm_path = Path(pkm_path)
    if not pkm_path.exists():
        raise PkmError(f"file not found: {pkm_path}")

    data = pkging.tar_files(pkm_path)
    for required in ("pkm.toml", "SIGNATURE"):
        if required not in data:
            raise PkmError(f"{pkm_path.name} is missing {required}; build with pkm build (adds a signature)")

    mf = parse_manifest(data["pkm.toml"].decode())
    sig_b64, kid = pkging.parse_signature(data["SIGNATURE"].decode())
    digest = pkging.digest_from(data)

    pub_b64 = signing.pubkey_b64_for(kid)
    if not signing.verify_bytes(pub_b64, digest, sig_b64):
        raise PkmError("invalid signature: make sure the publishing key matches the build key, then re-run pkm build")

    fname = f"{mf.name}-{mf.version}.pkm"
    dest_dir = REGISTRY_PACKAGES / mf.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fname
    shutil.copy2(pkm_path, dest)

    file_sha = _file_sha(dest)
    idx = load_index()
    idx["packages"].setdefault(mf.name, {})[mf.version] = {
        "file": fname,
        "version": mf.version,
        "sha256": file_sha,
        "pubkey": pub_b64,
        "key_id": kid,
        "published_at": now(),
    }
    save_index(idx)
    _git_commit(f"publish {mf.name} {mf.version}")

    print(f"✓ published {mf.name}@{mf.version}")
    print(f"  location: {dest}")
    print(f"  sha256:   {file_sha}")


def _file_sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get(name: str, version=None):
    """Returns (entry, file path). version=None picks the latest."""
    idx = load_index()
    versions = idx["packages"].get(name)
    if not versions:
        raise PkmError(f"package {name} not found in registry (run pkm build && pkm publish first)")
    if version is None:
        version = max(versions, key=lambda v: parse_version(v))
    if version not in versions:
        available = ", ".join(sorted(versions))
        raise PkmError(f"{name} has no {version} version. Available: {available}")
    entry = versions[version]
    path = REGISTRY_PACKAGES / name / entry["file"]
    if not path.exists():
        raise PkmError(f"registry file missing (index/store out of sync): {path}")
    return entry, path


def _git_commit(msg: str) -> None:
    """Auto git init + commit on publish; failures are silent (works without git too)."""
    try:
        if not (REGISTRY_DIR / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=REGISTRY_DIR, check=False, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=REGISTRY_DIR, check=False, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", msg],
            cwd=REGISTRY_DIR,
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass  # git not installed


def _require_git_repo() -> None:
    if not (REGISTRY_DIR / ".git").exists():
        raise PkmError("registry is not a git repo yet (publish once first, or run git init manually)")


def git_push() -> None:
    _require_git_repo()
    r = subprocess.run(["git", "remote"], cwd=REGISTRY_DIR, capture_output=True, text=True)
    if "origin" not in r.stdout.split():
        print("No remote configured yet. Run:")
        print("  git remote add origin git@github.com:<you>/pkm-registry.git")
        print(f"  git -C {REGISTRY_DIR} push -u origin main")
        return
    subprocess.run(["git", "push"], cwd=REGISTRY_DIR)


def git_pull() -> None:
    _require_git_repo()
    subprocess.run(["git", "pull"], cwd=REGISTRY_DIR)
