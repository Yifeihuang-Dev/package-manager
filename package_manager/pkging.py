"""The `.pkm` package format, and building/extraction.

Package layout (tar.gz):
    pkm.toml      manifest
    <name>/...    the code itself (packed as-is, zero rewriting)
    SIGNATURE     "ed25519 <key_id> <signature_b64>"; signs the digest of every other file
"""

import hashlib
import io
import shutil
import tarfile
import tempfile
from pathlib import Path

from . import signing
from .errors import PkmError
from .manifest import parse_manifest

EXCLUDE_DIR_NAMES = {"__pycache__", ".git", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
EXCLUDE_FILE_NAMES = {".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def tar_files(path: Path) -> dict:
    """Read every regular file inside a .pkm, as {arcname: bytes}."""
    data = {}
    with tarfile.open(path, "r:gz") as tf:
        for m in tf.getmembers():
            if m.isfile():
                f = tf.extractfile(m)
                if f is not None:
                    data[m.name] = f.read()
    return data


def digest_from(data: dict) -> bytes:
    """Digest = sha256 over sorted(path + sha256(file)) for all files (SIGNATURE excluded)."""
    h = hashlib.sha256()
    for name in sorted(data):
        if name == "SIGNATURE":
            continue
        h.update(name.encode())
        h.update(hashlib.sha256(data[name]).digest())
    return h.digest()


def parse_signature(text: str):
    """Returns (signature_b64, key_id, embedded_pubkey_b64_or_None).

    The 4th field (embedded public key, added in 0.2.0) lets standalone .pkm
    files / URL installs verify signatures without a registry index.
    """
    parts = text.split()
    if len(parts) not in (3, 4) or parts[0] != "ed25519":
        raise PkmError(f"malformed SIGNATURE: {text[:60]!r}")
    return parts[2], parts[1], (parts[3] if len(parts) == 4 else None)


def read_manifest_text(path: Path) -> str:
    data = tar_files(path)
    if "pkm.toml" not in data:
        raise PkmError(f"{Path(path).name} has no pkm.toml; not a valid .pkm package")
    return data["pkm.toml"].decode()


def _collect_code(code_dir: Path):
    """Recursively collect code files as [(arcname, Path)], excluding caches and junk."""
    results = []
    for p in sorted(code_dir.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(code_dir.parent).as_posix()
        if any(part in EXCLUDE_DIR_NAMES for part in p.parts):
            continue
        if p.name in EXCLUDE_FILE_NAMES or p.suffix in EXCLUDE_SUFFIXES:
            continue
        results.append((rel, p))
    return results


def build(src: Path, out=None):
    """Source directory -> <name>-<version>.pkm. Returns (output path, file sha256)."""
    src = Path(src)
    mf_path = src / "pkm.toml"
    if not mf_path.exists():
        raise PkmError(f"{src} is missing pkm.toml ([package] name / version are required)")

    mf = parse_manifest(mf_path.read_text())
    code_dir = src / mf.name
    if not code_dir.is_dir():
        raise PkmError(
            f"missing code directory {mf.name}/: the import name is this folder, it must match [package].name"
        )

    data = {"pkm.toml": mf_path.read_bytes()}
    for arcname, p in _collect_code(code_dir):
        data[arcname] = p.read_bytes()

    digest = digest_from(data)
    sig_b64, kid, pub_b64 = signing.sign_bytes(digest)
    # 4th field = embedded public key: standalone files/URL installs can verify without an index
    data["SIGNATURE"] = f"ed25519 {kid} {sig_b64} {pub_b64}".encode()

    if out:
        out_path = Path(out)
    else:
        out_path = Path.cwd() / f"{mf.name}-{mf.version}.pkm"

    with tarfile.open(out_path, "w:gz") as tf:
        for arcname in sorted(data):
            raw = data[arcname]
            info = tarfile.TarInfo(arcname)
            info.size = len(raw)
            info.mtime = 0
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(raw))

    file_sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
    return out_path, file_sha


def extract_code(pkm_path: Path, name: str, project_root: Path) -> None:
    """Extract the <name>/ directory from a .pkm into the project root, as-is (zero rewriting)."""
    meta_dir = project_root / ".pkm"
    meta_dir.mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="extract-", dir=meta_dir))
    try:
        with tarfile.open(pkm_path, "r:gz") as tf:
            tf.extractall(staging, filter="data")
        code = staging / name
        if not code.is_dir():
            raise PkmError(f"{Path(pkm_path).name} contains no {name}/ directory")
        target = project_root / name
        if target.exists():
            raise PkmError(f"target directory already exists: {target}")
        shutil.move(str(code), str(target))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
