"""Signing and trust (TOFU): keygen / sign / verify."""

import base64
import hashlib
import os
import sys

from . import ed25519
from .config import KEYS_DIR, KNOWN_KEYS_FILE, REGISTRY_INDEX
from .errors import PkmError
from .util import load_json, save_json


def _key_file(name="default"):
    return KEYS_DIR / f"{name}.key"


def _pub_file(name="default"):
    return KEYS_DIR / f"{name}.pub"


def keygen(name="default") -> str:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    if _key_file(name).exists():
        raise PkmError(f"key {name} already exists ({_key_file(name)}); delete it first to reset")
    seed = os.urandom(32)
    pub = ed25519.public_key(seed)
    _key_file(name).write_bytes(seed)
    os.chmod(_key_file(name), 0o600)
    _pub_file(name).write_bytes(pub)

    kid = key_id(pub)
    known = load_json(KNOWN_KEYS_FILE, {})
    known[kid] = base64.b64encode(pub).decode()
    save_json(KNOWN_KEYS_FILE, known)

    print(f"✓ signing keypair generated ({name})")
    print(f"  private key: {_key_file(name)} (never share it — losing it means losing your publishing identity)")
    print(f"  public key:  {_pub_file(name)}")
    print(f"  key_id:      {kid}")
    return kid


def load_seed(name="default") -> bytes:
    p = _key_file(name)
    if not p.exists():
        raise PkmError("no signing key yet; run: pkm keygen")
    seed = p.read_bytes()
    if len(seed) != 32:
        raise PkmError(f"corrupted key file: {p}")
    return seed


def key_id(pub: bytes) -> str:
    return hashlib.sha256(pub).hexdigest()[:16]


def sign_bytes(data: bytes):
    """Returns (signature_b64, key_id, public_key_b64)."""
    seed = load_seed()
    pub = ed25519.public_key(seed)
    sig = ed25519.sign(seed, data)
    return base64.b64encode(sig).decode(), key_id(pub), base64.b64encode(pub).decode()


def verify_bytes(pub_b64: str, data: bytes, sig_b64: str) -> bool:
    try:
        pub = base64.b64decode(pub_b64)
        sig = base64.b64decode(sig_b64)
    except Exception:
        return False
    return ed25519.verify(pub, data, sig)


def tofu_check(kid: str, pub_b64: str) -> None:
    """Trust On First Use: record the fingerprint the first time it is seen; afterwards it must match."""
    known = load_json(KNOWN_KEYS_FILE, {})
    if kid in known:
        if known[kid] != pub_b64:
            raise PkmError(f"key {kid} does not match the recorded public key; refusing to install (possible impersonation)")
        return

    trusted = False
    if sys.stdin.isatty():
        ans = input(f"first time seeing signing key {kid}; trust and record it? [y/N] ").strip().lower()
        trusted = ans == "y"
    if not trusted:
        if not sys.stdin.isatty():
            print(f"! non-interactive mode: recording new key {kid} automatically (TOFU)")
        else:
            raise PkmError("cancelled; key was not trusted")
    known[kid] = pub_b64
    save_json(KNOWN_KEYS_FILE, known)
    print(f"✓ signing key {kid} recorded")


def keys_list() -> None:
    if not KEYS_DIR.exists() or not list(KEYS_DIR.glob("*.key")):
        print("(empty) no keys yet; run: pkm keygen")
        return
    for kf in sorted(KEYS_DIR.glob("*.key")):
        name = kf.stem
        pf = KEYS_DIR / f"{name}.pub"
        if not pf.exists():
            print(f"{name:<10} (public key file missing)")
            continue
        pub = pf.read_bytes()
        print(f"{name:<10} key_id={key_id(pub)}  fingerprint={hashlib.sha256(pub).hexdigest()}")


def trust_list() -> None:
    """Visualize the TOFU trusted store: every publisher you have ever accepted."""
    known = load_json(KNOWN_KEYS_FILE, {})
    if not known:
        print("(empty) no trusted publisher keys yet — they appear here after the first [y] in TOFU")
        return
    my_kid = None
    if _key_file().exists():
        try:
            my_kid = key_id(ed25519.public_key(load_seed()))
        except PkmError:
            pass
    # which local packages each key has signed (best-effort annotation)
    owners = {}
    idx = load_json(REGISTRY_INDEX, {})
    for pkg, versions in idx.get("packages", {}).items():
        for ver, entry in versions.items():
            kid = entry.get("key_id")
            if kid:
                owners.setdefault(kid, set()).add(f"{pkg}@{ver}")
    print(f"{len(known)} trusted publisher key(s):")
    for kid, pub in sorted(known.items()):
        mark = "   <- your own key" if kid == my_kid else ""
        print(f"  {kid}  {hashlib.sha256(base64.b64decode(pub)).hexdigest()}{mark}")
        pkgs = ", ".join(sorted(owners.get(kid, []))) or "(no local packages signed by this key)"
        print(f"    signed: {pkgs}")


def trust_remove(kid: str) -> None:
    """Revoke trust in a publisher key (e.g. after key compromise / publisher change)."""
    known = load_json(KNOWN_KEYS_FILE, {})
    if kid not in known:
        raise PkmError(f"key {kid} is not in the trusted list (see: pkm trust list)")
    del known[kid]
    save_json(KNOWN_KEYS_FILE, known)
    print(f"✓ key {kid} revoked — its packages will now fail verification until you re-trust it")


def pubkey_b64_for(kid: str) -> str:
    """Find the public key for a key_id: local key first, then the trusted list."""
    pub = lookup_pubkey(kid, include_local_key=True)
    if pub is None:
        raise PkmError(f"no public key found for signing key {kid}; cannot verify")
    return pub


def lookup_pubkey(kid: str, include_local_key: bool = False):
    """Best-effort public key lookup; returns None when unknown.

    Order: local keypair (optional) -> TOFU known_keys -> local registry index.
    """
    if include_local_key and _key_file().exists():
        try:
            pub = ed25519.public_key(load_seed())
            if key_id(pub) == kid:
                return base64.b64encode(pub).decode()
        except PkmError:
            pass
    known = load_json(KNOWN_KEYS_FILE, {})
    if kid in known:
        return known[kid]
    idx = load_json(REGISTRY_INDEX, {})
    for versions in idx.get("packages", {}).values():
        for entry in versions.values():
            if entry.get("key_id") == kid and entry.get("pubkey"):
                return entry["pubkey"]
    return None
