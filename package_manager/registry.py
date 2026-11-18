"""Local registry: package store + index.json + optional git sync.

The registry is just a directory (~/.pkm/registry). Publishing auto-runs git init/commit;
once a remote is configured, `pkm registry push/pull` syncs it across machines.
"""

import shutil
import subprocess
from pathlib import Path

from . import pkging, signing, sources
from .config import REGISTRY_DIR, REGISTRY_INDEX, REGISTRY_PACKAGES, REMOTE_CACHE
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
    sig_b64, kid, _embedded_pub = pkging.parse_signature(data["SIGNATURE"].decode())
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


def search(query: str) -> None:
    """Search local registry + all configured remote sources by package-name substring."""
    q = query.lower()
    results = {}  # name -> (versions, sources)
    idx = load_json(REGISTRY_INDEX, {})
    for name, versions in idx.get("packages", {}).items():
        if q in name.lower():
            results[name] = (sorted(versions), {"local"})
    for sname, base in sorted(sources.load_sources().items()):
        try:
            ridx = sources.fetch_json(f"{base}/index.json")
        except PkmError:
            print(f"! source {sname} unreachable, skipped")
            continue
        for name, versions in ridx.get("packages", {}).items():
            if q in name.lower():
                if name in results:
                    results[name][1].add(sname)
                else:
                    results[name] = (sorted(versions), {sname})
    if not results:
        print(f"(no match) nothing found for '{query}'")
        return
    print(f"{len(results)} package(s) matching '{query}':")
    for name, (versions, where) in sorted(results.items()):
        src = "+".join(sorted(where))
        print(f"  {name}  versions: {', '.join(versions)}  [{src}]")
        print(f"    install: pkm install {name}")


def get(name: str, version=None):
    """Returns (entry, file path). version=None picks the latest.

    Local registry first; on a miss, falls back to configured remote sources
    (pkm source add), downloading the package into the remote cache.
    """
    versions = load_index()["packages"].get(name)
    if versions and (version is None or version in versions):
        if version is None:
            version = max(versions, key=lambda v: parse_version(v))
        entry = versions[version]
        path = REGISTRY_PACKAGES / name / entry["file"]
        if path.exists():
            return entry, path

    # remote fallback: try each configured source in turn
    all_sources = sources.load_sources()
    local_msg = f"package {name} not found in local registry (run pkm build && pkm publish first)"
    last_err = PkmError(local_msg)
    for sname, base in sorted(all_sources.items()):
        try:
            idx = sources.fetch_json(f"{base}/index.json")
            rversions = idx.get("packages", {}).get(name)
            if not rversions:
                last_err = PkmError(f"package {name} not found in source {sname}")
                continue
            ver = version or max(rversions, key=lambda v: parse_version(v))
            if ver not in rversions:
                last_err = PkmError(f"{name} has no {ver} version in source {sname}. Available: {', '.join(sorted(rversions))}")
                continue
            entry = rversions[ver]
            fname = entry["file"]
            file_url = fname if fname.startswith(("http://", "https://")) else f"{base}/packages/{name}/{fname}"
            dest_dir = REMOTE_CACHE / name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{name}-{ver}.pkm"
            dest.write_bytes(sources.fetch(file_url))
            print(f"✓ fetched {name}@{ver} from source {sname}")
            return entry, dest
        except PkmError as e:
            last_err = e
    raise last_err


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
