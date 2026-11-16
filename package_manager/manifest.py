"""Parsing and validation of the pkm.toml manifest."""

import re
import tomllib
from dataclasses import dataclass, field

from .errors import PkmError

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
VER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class Manifest:
    name: str
    version: str
    description: str = ""
    dependencies: dict = field(default_factory=dict)


def parse_version(v: str):
    """'1.2.0' -> (1, 2, 0). Exact semver only — pkm's first line of defense against dependency hell."""
    if not VER_RE.match(str(v)):
        raise PkmError(f"version must be exact X.Y.Z format, got: {v}")
    return tuple(int(x) for x in str(v).split("."))


def parse_manifest(text: str) -> Manifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PkmError(f"failed to parse pkm.toml: {e}")

    pkg = data.get("package")
    if not isinstance(pkg, dict) or "name" not in pkg or "version" not in pkg:
        raise PkmError("pkm.toml is missing [package] name / version")

    name = str(pkg["name"])
    version = str(pkg["version"])
    if not NAME_RE.match(name):
        raise PkmError(f"invalid package name (lowercase letters, digits, '-', '_' only): {name}")
    parse_version(version)

    deps = data.get("dependencies", {}) or {}
    for dn, dv in deps.items():
        if not NAME_RE.match(str(dn)):
            raise PkmError(f"invalid dependency name: {dn}")
        parse_version(str(dv))

    return Manifest(
        name=name,
        version=version,
        description=str(pkg.get("description", "")),
        dependencies={str(k): str(v) for k, v in deps.items()},
    )


def parse_spec(spec: str):
    """'utils' or 'utils@1.2.0' -> (name, version|None)."""
    if "@" in spec:
        name, _, ver = spec.partition("@")
        name, ver = name.strip(), ver.strip()
        if ver:
            parse_version(ver)
    else:
        name, ver = spec.strip(), None

    if not name or not NAME_RE.match(name):
        raise PkmError(f"invalid package name: {spec}")
    return name, ver
