"""Streaming (cloud-mode) imports: code lives in the cloud, never on disk.

    pkm use utils                  # record intent (metadata only) -> ./.pkm/cloud.json
    pkm use logkit --latest        # follow the newest version instead of pinning
    python -m package_manager.cloud -c "import utils"    # code streams in, verified, memory-only
    python -m package_manager.cloud app.py               # run a script with the hook installed
    python -m package_manager.cloud                      # interactive REPL

Flow: fetch index.json -> download .pkm -> verify sha256 + Ed25519 signature
-> compile every module straight from bytes into sys.modules. The project
directory never receives code — only the tiny cloud.json manifest.

Resilience: every verified package is cached under ~/.pkm/cloud-cache/.
If the network is down, the cache is used and its signature is re-verified
offline (the .pkm embeds the signer public key, so no index is needed).
Version semantics: "name": "1.2.3" pins exactly; "name": "*" follows latest
with a drift notice when a pinned package has a newer release in the cloud.
Non-code resources: cloud.resource(pkg, relpath) and importlib.resources
both read straight from memory via the loader's get_data().
"""

import code as _code
import hashlib
import importlib.abc
import importlib.machinery
import runpy
import sys
import tarfile
from io import BytesIO
from pathlib import Path

from . import pkging, signing, sources
from .config import CLOUD_CACHE
from .errors import PkmError
from .manifest import parse_manifest, parse_version
from .util import load_json, save_json

try:  # ResourceReader moved in 3.13
    from importlib.resources.abc import ResourceReader
except ImportError:  # pragma: no cover
    from importlib.abc import ResourceReader

# name -> {"files": {arcname: bytes}, "version": str, "entry": {...}}
_STREAMS = {}


# ---------------------------------------------------------------- core


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
    """Same double gate as project._verify_package, for an in-memory package.
    Works offline: when no index entry is given, the embedded public key
    (or the TOFU store) proves the signature."""
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


def _cache_path(name: str, ver: str) -> Path:
    return CLOUD_CACHE / name / f"{name}-{ver}.pkm"


def load_stream(name: str, version=None) -> str:
    """Download + verify + hold in memory (and cache a verified copy). Returns the version loaded."""
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
            cp = _cache_path(name, ver)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_bytes(raw)  # verified copy, for offline fallback
            print(f"✓ streamed {name}@{ver} from source {sname}  (code in memory, not on disk)")
            # stream the dependency chain too — code deps live in the cloud as well
            deps = parse_manifest(data["pkm.toml"].decode()).dependencies
            for dep_name, dep_ver in sorted(deps.items()):
                if dep_name not in _STREAMS:
                    ensure_stream(dep_name, dep_ver)
            return ver
        except PkmError as e:
            last_err = e
    raise last_err


def _load_offline(name: str, pinned) -> str:
    """Offline fallback: stream from the verified local cache (signature re-checked)."""
    if pinned:
        candidates = [pinned] if _cache_path(name, pinned).exists() else []
    else:
        d = CLOUD_CACHE / name
        candidates = []
        if d.exists():
            for p in d.glob(f"{name}-*.pkm"):
                candidates.append(p.stem[len(name) + 1:])
    if not candidates:
        raise PkmError(f"offline and no verified cache for {name}" + (f"@{pinned}" if pinned else ""))
    ver = max(candidates, key=parse_version)
    raw = _cache_path(name, ver).read_bytes()
    data = _tar_files_bytes(raw)
    _verify_stream(name, ver, raw, data, {})  # embedded pubkey / TOFU — no index needed
    _STREAMS[name] = {"files": data, "version": ver, "entry": {}}
    print(f"! offline: using verified cached copy of {name}@{ver}")
    return ver


def ensure_stream(name: str, ver_spec) -> None:
    """ver_spec: "x.y.z" pins exactly, "*" follows latest, version string = explicit.
    Handles offline fallback and the drift notice for pinned packages."""
    pinned = None if ver_spec in (None, "*") else ver_spec
    try:
        ver = load_stream(name, pinned)
    except PkmError:
        _load_offline(name, pinned)
        return
    if pinned and ver == pinned:
        try:
            latest = resolve_version(name)
            if latest != pinned:
                print(f"! {name}: cloud has newer {latest} (pinned {pinned}); "
                      f"run: pkm use {name} --latest")
        except PkmError:
            pass  # drift check is best-effort


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


# ---------------------------------------------------------------- resources


def resource(pkg: str, relpath: str) -> bytes:
    """Read a non-code file from a streamed package, straight from memory."""
    if pkg not in _STREAMS:
        raise PkmError(f"{pkg} is not streamed; import it first or add it with pkm use")
    key = f"{pkg}/{relpath.lstrip('/')}"
    files = _STREAMS[pkg]["files"]
    if key not in files:
        raise PkmError(f"{key} not in streamed package {pkg}")
    return files[key]


# ---------------------------------------------------------------- import hook


class _ResourceReader(ResourceReader):
    """Standard importlib.resources protocol over an in-memory package tree."""

    def __init__(self, name: str, stream: dict, base: str):
        self.name = name
        self.stream = stream
        self.base = base  # e.g. "utils" or "utils/net"

    def _key(self, rel: str) -> str:
        rel = str(rel).lstrip("/")
        return f"{self.base}/{rel}" if rel else self.base

    def open_resource(self, resource):
        key = self._key(resource)
        if key in self.stream["files"]:
            return BytesIO(self.stream["files"][key])
        raise FileNotFoundError(key)

    def resource_path(self, resource):
        raise FileNotFoundError(f"in-memory package {self.name} has no filesystem path for {resource}")

    def is_resource(self, path):
        key = self._key(path)
        return key in self.stream["files"] and not key.endswith("__init__.py")

    def contents(self):
        prefix = self.base + "/"
        names = set()
        for k in self.stream["files"]:
            if k.startswith(prefix):
                first = k[len(prefix):].split("/", 1)[0]
                if first:
                    names.add(first)
        return iter(sorted(names))


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

    def get_data(self, path):
        """Resolve virtual <cloud pkg@ver>/... paths from memory (file-protocol fallback)."""
        for name, stream in _STREAMS.items():
            prefix = f"<cloud {name}@{stream['version']}>/"
            if path.startswith(prefix):
                rel = path[len(prefix):]
                if rel in stream["files"]:
                    return stream["files"][rel]
        raise OSError(f"resource not found in any streamed package: {path}")

    def get_resource_reader(self, fullname):
        """Standard importlib.resources support (py3.10+: resources.files/read_bytes)."""
        top = fullname.split(".")[0]
        stream = _STREAMS.get(top)
        if stream is None:
            return None
        return _ResourceReader(top, stream, fullname.replace(".", "/"))


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
            # virtual search location: makes importlib.resources work via loader.get_data
            ver = _STREAMS[top]["version"]
            spec.submodule_search_locations = [f"<cloud {top}@{ver}>/{base}"]
        return spec


def install_hook() -> None:
    if not any(isinstance(f, _StreamFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _StreamFinder())


# ------------------------------------------------------------- use / entrypoint

CLOUD_META = ".pkm/cloud.json"


def use(spec: str, latest: bool = False) -> None:
    """pkm use name|name@version [--latest]: record the cloud manifest only (zero code on disk)."""
    name, _, ver = spec.partition("@")
    if latest:
        record = "*"
        resolved = resolve_version(name, ver or None)
    else:
        resolved = resolve_version(name, ver or None)
        record = resolved
    meta_path = Path.cwd() / CLOUD_META
    cloud = load_json(meta_path, {}) if meta_path.exists() else {}
    cloud[name] = record
    save_json(meta_path, cloud)
    mode = "follow latest (*)" if latest else f"pinned {resolved}"
    print(f"✓ cloud use recorded: {name}  ({mode})  ->  {CLOUD_META}")
    print(f"  run: python -m package_manager.cloud   (code streams from the cloud at import time)")


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd()
    meta_path = root / CLOUD_META
    if not meta_path.exists():
        raise PkmError(f"no {CLOUD_META} here; record cloud packages first: pkm use <name>")

    # when run as `python -m package_manager.cloud`, this file executes as __main__;
    # rebind it so user code's `from package_manager import cloud` sees the SAME
    # module instance (and therefore the same _STREAMS) as the import hook.
    import package_manager

    sys.modules["package_manager.cloud"] = sys.modules[__name__]
    package_manager.cloud = sys.modules[__name__]

    cloud = load_json(meta_path, {})
    if not cloud:
        raise PkmError(f"{CLOUD_META} is empty; record cloud packages first: pkm use <name>")

    install_hook()
    for name, ver_spec in cloud.items():
        ensure_stream(name, ver_spec)

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
