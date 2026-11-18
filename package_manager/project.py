"""Consumer-side project operations: install / upgrade / remove / list.

Core vendoring semantics:
  - package code directories are extracted as-is into the project root (layout = import path, zero rewriting)
  - install metadata lives in the project's .pkm/installed.json (keeps the code tree clean)
  - the project's pkm.toml [dependencies] is kept in sync with exact versions
"""

import shutil
import tomllib
from pathlib import Path

from . import pkging, registry, signing, sources
from .errors import PkmError
from .manifest import parse_manifest, parse_spec, parse_version
from .toml_edit import remove_dependency, set_dependency
from .util import load_json, now, save_json

META_DIR = ".pkm"
INSTALLED_FILE = "installed.json"


# ---------- project-local metadata ----------

def _root() -> Path:
    return Path.cwd()


def _installed_path(root: Path) -> Path:
    d = root / META_DIR
    d.mkdir(exist_ok=True)
    return d / INSTALLED_FILE


def _load_installed(root: Path) -> dict:
    return load_json(_installed_path(root), {})


def _save_installed(root: Path, data: dict) -> None:
    save_json(_installed_path(root), data)


def _project_manifest_path(root: Path) -> Path:
    return root / "pkm.toml"


def _project_deps(root: Path) -> dict:
    p = _project_manifest_path(root)
    if not p.exists():
        return {}
    with open(p, "rb") as f:
        data = tomllib.load(f)
    deps = data.get("dependencies", {}) or {}
    return {str(k): str(v) for k, v in deps.items()}


# ---------- verification and resolution ----------

def _verify_package(path: Path, entry: dict | None = None) -> None:
    """Double gate before installing: file sha256 (when an index entry provides one)
    + signature verification (pubkey from the index entry, embedded in SIGNATURE,
    or the TOFU trusted store) + TOFU key record."""
    import hashlib

    path = Path(path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = (entry or {}).get("sha256")
    if expected and actual != expected:
        raise PkmError(f"{path.name} checksum mismatch; the file may have been tampered with")

    data = pkging.tar_files(path)
    if "SIGNATURE" not in data:
        raise PkmError(f"{path.name} has no signature; refusing to install")
    sig_b64, kid, embedded_pub = pkging.parse_signature(data["SIGNATURE"].decode())
    pub_b64 = (entry or {}).get("pubkey") or embedded_pub or signing.lookup_pubkey(kid)
    if not pub_b64:
        raise PkmError(
            f"cannot verify {path.name}: unknown key {kid} and no embedded public key; "
            "rebuild with pkm >= 0.2.0 (build embeds the pubkey) or install via a registry index"
        )
    digest = pkging.digest_from(data)
    if not signing.verify_bytes(pub_b64, digest, sig_b64):
        raise PkmError(f"{path.name} signature verification failed; refusing to install")
    signing.tofu_check(kid, pub_b64)


def _conflict_error(name: str, reqs) -> PkmError:
    lines = [f"dependency conflict: {name} is required at multiple different versions"]
    for ver, chain in reqs:
        lines.append(f"  {' -> '.join(chain)}  (requires {ver or 'latest'})")
    lines.append("  hint: align the version numbers in each package's [dependencies]")
    return PkmError("\n".join(lines))


def _resolve(specs):
    """Exact-version dependency resolution (BFS + explicit conflict errors, no range solving).

    Returns {name: {"version", "entry", "path", "manifest"}}
    """
    reqs = {}
    resolved = {}
    queue = [((n, v), [n]) for n, v in specs]

    while queue:
        (name, ver), chain = queue.pop(0)
        reqs.setdefault(name, []).append((ver, list(chain)))
        entry, path = registry.get(name, ver)
        _verify_package(path, entry)
        mf = parse_manifest(pkging.read_manifest_text(path))
        if mf.name != name:
            raise PkmError(f"package name mismatch: registry says {name}, manifest says {mf.name}")

        if name in resolved:
            prev = resolved[name]["version"]
            if ver and ver != prev:
                raise _conflict_error(name, reqs[name])
            continue

        resolved[name] = {"version": mf.version, "entry": entry, "path": path, "manifest": mf}
        for dep, dver in mf.dependencies.items():
            queue.append(((dep, dver), chain + [dep]))

    for name, lst in reqs.items():
        pinned = {v for v, _ in lst if v is not None}
        if len(pinned) > 1:
            raise _conflict_error(name, lst)
    return resolved


# ---------- the four consumer commands ----------

def _is_path_or_url(spec: str) -> bool:
    return spec.startswith(("http://", "https://")) or spec.endswith(".pkm")


def install(spec=None, force: bool = False) -> None:
    root = _root()

    # A .pkm file path or http(s) URL installs standalone (signature-verified)
    if spec and _is_path_or_url(spec):
        _install_direct(spec, root, force)
        return

    # Always resolve together with ALL deps already in the project manifest:
    # otherwise a new package's transitive deps would bypass existing version
    # constraints, causing silent downgrades / unreported conflicts.
    deps = _project_deps(root)
    if spec:
        name, ver = parse_spec(spec)
        if ver is None:
            ver = deps.get(name)  # keep the existing pin if the package is already listed
        deps[name] = ver
    if not deps:
        raise PkmError("no package name given, and the current pkm.toml has no [dependencies]")

    resolved = _resolve(list(deps.items()))
    _apply(root, resolved, force)


def _install_direct(spec: str, root: Path, force: bool) -> None:
    """Install a standalone .pkm (local file or http(s) URL): verify -> extract -> record."""
    import hashlib
    import tempfile

    cleanup = None
    if spec.startswith(("http://", "https://")):
        staging_root = root / META_DIR
        staging_root.mkdir(parents=True, exist_ok=True)  # fresh project may not have it yet
        staging = Path(tempfile.mkdtemp(prefix="download-", dir=staging_root))
        fname = spec.split("?")[0].rstrip("/").split("/")[-1] or "package"
        if not fname.endswith(".pkm"):
            fname += ".pkm"
        path = staging / fname
        path.write_bytes(sources.fetch(spec))
        cleanup = staging
        print(f"✓ downloaded {fname}")
    else:
        path = Path(spec).expanduser().resolve()
        if not path.exists():
            raise PkmError(f"file not found: {spec}")

    try:
        _verify_package(path, entry=None)
        mf = parse_manifest(pkging.read_manifest_text(path))
        if mf.name in mf.dependencies:
            raise PkmError(f"{mf.name} cannot depend on itself")

        file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        data = pkging.tar_files(path)
        _, kid, _ = pkging.parse_signature(data["SIGNATURE"].decode())

        # guard against a conflicting pin in the project manifest
        prev = _project_deps(root).get(mf.name)
        if prev and prev != mf.version:
            raise _conflict_error(mf.name, [
                (mf.version, [mf.name]),
                (prev, ["current pkm.toml"]),
            ])

        direct = {
            "version": mf.version,
            "entry": {"sha256": file_sha, "key_id": kid},
            "path": path,
            "manifest": mf,
        }
        # resolve the package's own deps together with the project's existing pins
        deps = {**_project_deps(root), **mf.dependencies}
        deps.pop(mf.name, None)
        resolved = _resolve(list(deps.items())) if deps else {}
        resolved[mf.name] = direct
        _apply(root, resolved, force)
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


def _apply(root: Path, resolved: dict, force: bool) -> None:
    """Extract every resolved package into the project root and record metadata."""
    installed = _load_installed(root)

    for name in sorted(resolved):
        info = resolved[name]
        entry = info["entry"]
        target = root / name
        existing = installed.get(name)

        if target.exists():
            if existing and existing["version"] == info["version"] and not force:
                print(f"- {name}@{info['version']} already installed, skipping (--force to overwrite)")
                continue
            if not existing and not force:
                raise PkmError(f"directory ./{name}/ already exists and was not installed by pkm; use --force to overwrite")
            shutil.rmtree(target)

        pkging.extract_code(info["path"], name, root)
        installed[name] = {
            "version": info["version"],
            "sha256": entry.get("sha256", ""),
            "key_id": entry.get("key_id", ""),
            "installed_at": now(),
        }
        print(f"✓ installed {name}@{info['version']}  ->  ./{name}/")

    _save_installed(root, installed)
    _sync_project_toml(root, resolved)


def _sync_project_toml(root: Path, resolved: dict) -> None:
    p = _project_manifest_path(root)
    text = p.read_text() if p.exists() else ""
    for name in sorted(resolved):
        text = set_dependency(text, name, resolved[name]["version"])
    p.write_text(text)


def upgrade(name=None) -> None:
    root = _root()
    installed = _load_installed(root)
    targets = [name] if name else sorted(installed)
    if not targets:
        raise PkmError("no pkm packages installed in this project")

    for t in targets:
        if t not in installed:
            raise PkmError(f"{t} is not installed; run pkm install {t} first")
        entry, path = registry.get(t, None)  # latest
        _verify_package(path, entry)
        mf = parse_manifest(pkging.read_manifest_text(path))
        cur = installed[t]["version"]
        if parse_version(mf.version) <= parse_version(cur):
            print(f"- {t}@{cur} is up to date (registry latest: {mf.version})")
            continue

        target = root / t
        if target.exists():
            shutil.rmtree(target)
        pkging.extract_code(path, t, root)
        installed[t].update(
            {"version": mf.version, "sha256": entry.get("sha256", ""), "installed_at": now()}
        )
        p = _project_manifest_path(root)
        if p.exists():
            p.write_text(set_dependency(p.read_text(), t, mf.version))
        print(f"✓ {t}: {cur} -> {mf.version}")

    _save_installed(root, installed)


def remove(name: str) -> None:
    root = _root()
    installed = _load_installed(root)
    if name not in installed:
        raise PkmError(f"{name} is not in the installed list (see pkm list)")

    target = root / name
    if target.exists():
        shutil.rmtree(target)
    del installed[name]
    _save_installed(root, installed)

    p = _project_manifest_path(root)
    if p.exists():
        p.write_text(remove_dependency(p.read_text(), name))
    print(f"✓ removed {name}")


def list_installed() -> None:
    root = _root()
    installed = _load_installed(root)
    if not installed:
        print("(empty) no pkm packages installed in this project; try: pkm install <name>")
        return
    print(f"{'package':<16} {'version':<10} status")
    for name, info in sorted(installed.items()):
        status = "ok" if (root / name).exists() else "directory missing!"
        print(f"{name:<16} {info['version']:<10} {status}  key={info.get('key_id', '?')}")
