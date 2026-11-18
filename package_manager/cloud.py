"""Streaming (cloud-mode) imports: code lives in the cloud, never on disk.

    pkm use utils                 # record intent (metadata only) -> ./.pkm/cloud.json
    python -m package_manager.cloud -c "import utils"    # code streams in, verified, memory-only
    python -m package_manager.cloud app.py               # run a script with the hook installed
    python -m package_manager.cloud                      # interactive REPL

Flow: fetch index.json -> download .pkm -> verify sha256 + Ed25519 signature
-> compile every module straight from bytes into sys.modules. The project
directory never receives code — only the tiny cloud.json manifest.
"""

import code as _code
import importlib.abc
import importlib.machinery
import runpy
import sys
import tarfile
from io import BytesIO
from pathlib import Path

from . import pkging, signing, sources
from .errors import PkmError
from .manifest import parse_manifest, parse_version

# name -> {"files": {arcname: bytes}, "version": str, "entry": {...}}
_STREAMS = {}


def _tar_files_bytes(raw: bytes) -> dict:
    """Same as pkging.tar_files but from an in-memory archive."""
    data = {}
    with tarfile.open(fileobj=BytesIO(raw), mode="r:gz") as tf:
        for m in tf.getmembers():
            if m.isfile():
                f = tf.extractfile(m)
                if f is not None:
                    data[m.name] = f.read()
    return data


def _verify_stream(name: str, ver: str, raw: bytes, data: dict, entry: dict) -> None:
    """Same double gate as project._verify_package, for an in-memory package."""
    import hashlib

    actual = hashlib.sha256(raw).hexdigest()
    expected = (entry or {}).get("sha256")
    if expected and actual != expected:
        raise PkmError(f"{name}@{ver} checksum mismatch; refusing to stream")

    if "SIGNATURE" not in data:
        raise PkmError(f"{name}@{ver} has no signature; refusing to stream")
    sig_b64, kid, embedded_pub = pkging.parse_signature(data["SIGNATURE"].decode())
    pub_b64 = (entry or {}).get("pubkey") or embedded_pub or signing.lookup_pubkey(kid)
    if not pub_b64:
        raise PkmError(f"cannot verify {name}@{ver}: unknown key {kid} and no embedded public key")
    if not signing.verify_bytes(pub_b64, pkging.digest_from(data), sig_b64):
        raise PkmError(f"{name}@{ver} signature verification failed; refusing to stream")
    signing.tofu_check(kid, pub_b64)


def load_stream(name: str, version=None) -> str:
    """Download + verify + hold in memory. Returns the version loaded. Zero disk writes."""
    all_sources = sources.load_sources()
    if not all_sources:
        raise PkmError("no remote sources configured; run: pkm source add <name> <url>")
    last_err = PkmError(f"package {name} not found in any configured source")
    for sname, base in sorted(all_sources.items()):
        try:
            idx = sources.fetch_json(f"{base}/index.json")
            versions = idx.get("packages", {}).get(name)
            if not versions:
                last_err = PkmError(f"package {name} not found in source {sname}")
                continue
            ver = version or max(versions, key=lambda v: parse_version(v))
            if ver not in versions:
                last_err = PkmError(f"{name} has no {ver} version in source {sname}")
                continue
            entry = versions[ver]
            fname = entry["file"]
            file_url = fname if fname.startswith(("http://", "https://")) else f"{base}/packages/{name}/{fname}"
            raw = sources.fetch(file_url)
            data = _tar_files_bytes(raw)
            _verify_stream(name, ver, raw, data, entry)
            _STREAMS[name] = {"files": data, "version": ver, "entry": entry}
            print(f"✓ streamed {name}@{ver} from source {sname}  (code in memory, not on disk)")
            # stream the dependency chain too — code deps live in the cloud as well
            deps = parse_manifest(data["pkm.toml"].decode()).dependencies
            for dep_name, dep_ver in sorted(deps.items()):
                if dep_name not in _STREAMS:
                    load_stream(dep_name, dep_ver)
            return ver
        except PkmError as e:
            last_err = e
    raise last_err


def resolve_version(name: str, version=None) -> str:
    """Look up the latest (or given) version in the cloud index without downloading the package."""
    all_sources = sources.load_sources()
    if not all_sources:
        raise PkmError("no remote sources configured; run: pkm source add <name> <url>")
    last_err = PkmError(f"package {name} not found in any configured source")
    for sname, base in sorted(all_sources.items()):
        try:
            idx = sources.fetch_json(f"{base}/index.json")
            versions = idx.get("packages", {}).get(name)
            if not versions:
                last_err = PkmError(f"package {name} not found in source {sname}")
                continue
            ver = version or max(versions, key=lambda v: parse_version(v))
            if ver not in versions:
                last_err = PkmError(f"{name} has no {ver} version in source {sname}")
                continue
            return ver
        except PkmError as e:
            last_err = e
    raise last_err


# ---------------------------------------------------------------- import hook


class _StreamLoader(importlib.abc.Loader):
    def __init__(self, fullname: str):
        self.fullname = fullname

    def create_module(self, spec):
        return None  # default module creation

    def exec_module(self, module):
        top = self.fullname.split(".")[0]
        stream = _STREAMS[top]
        base = self.fullname.replace(".", "/")
        for candidate in (f"{base}/__init__.py", f"{base}.py"):
            src = stream["files"].get(candidate)
            if src is not None:
                module.__file__ = f"<cloud {top}@{stream['version']}>/{candidate}"
                exec(compile(src, module.__file__, "exec"), module.__dict__)
                return
        raise ImportError(f"{self.fullname} not found inside cloud package {top}")


class _StreamFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if top not in _STREAMS:
            return None
        files = _STREAMS[top]["files"]
        base = fullname.replace(".", "/")
        is_pkg = f"{base}/__init__.py" in files
        spec = importlib.machinery.ModuleSpec(fullname, _StreamLoader(fullname), is_package=is_pkg)
        if is_pkg:
            spec.submodule_search_locations = []  # keep subpackages memory-only
        return spec


def install_hook() -> None:
    if not any(isinstance(f, _StreamFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _StreamFinder())


# ------------------------------------------------------------- use / entrypoint

CLOUD_META = ".pkm/cloud.json"


def use(spec: str) -> None:
    """pkm use name|name@version: record the cloud manifest only (zero code on disk)."""
    from .util import load_json, save_json

    name, _, ver = spec.partition("@")
    ver = ver or None
    resolved = resolve_version(name, ver)
    meta_path = Path.cwd() / CLOUD_META
    cloud = load_json(meta_path, {}) if meta_path.exists() else {}
    cloud[name] = resolved
    save_json(meta_path, cloud)
    print(f"✓ cloud use recorded: {name}@{resolved}  ->  {CLOUD_META}")
    print(f"  run: python -m package_manager.cloud   (code streams from the cloud at import time)")


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd()
    meta_path = root / CLOUD_META
    if not meta_path.exists():
        raise PkmError(f"no {CLOUD_META} here; record cloud packages first: pkm use <name>")

    from .util import load_json

    cloud = load_json(meta_path, {})
    if not cloud:
        raise PkmError(f"{CLOUD_META} is empty; record cloud packages first: pkm use <name>")

    install_hook()
    for name, ver in cloud.items():
        load_stream(name, ver)

    # everything below runs with the cloud hook active
    if argv and argv[0] == "-c":
        exec(compile(" ".join(argv[1:]), "<cloud-exec>", "exec"), {"__name__": "__main__"})
    elif argv:
        runpy.run_path(argv[0], run_name="__main__")
    else:
        print("cloud REPL — streamed packages are importable, try: import utils")
        _code.interact(local={}, banner="", exitmsg="")


if __name__ == "__main__":
    try:
        main()
    except PkmError as e:
        print(f"✗ {e}")
        sys.exit(1)
