# pkm (Package Manager)

[English](README.md) | [简体中文](README.zh-CN.md)

A personal vendoring-style Python package manager. Pack, sign, and store your own code in a personal registry; then pull it into any project with one command — the files land **as-is in the project root**, immediately `import`able, zero magic.

## Design Principles

- **Vendoring mode**: `pkm install` extracts the package's code directory as-is into the project root. Layout = import path, nothing is rewritten. Code travels with the project — clone it and it works, no lockfile needed
- **Zero third-party dependencies**: pure Python standard library (including a built-in Ed25519 signature implementation)
- **Exact versions + explicit conflicts**: only `X.Y.Z` exact versions are supported. Diamond conflicts fail loudly with the full dependency chain, instead of being silently resolved — dependency hell is prevented, not solved
- **Signing (TOFU)**: every `.pkm` carries an Ed25519 signature; installs verify the signature and sha256 first. New key fingerprints are recorded on first use, and any mismatch afterwards is rejected

## Installation

```bash
pip install -e /path/to/package-manager   # installs the global `pkm` command
```

Or from a wheel:

```bash
pip install package_manager-0.1.0-py3-none-any.whl
```

## Quick Start

### Publisher: turn your code into a distributable package

```
mylib/
├── pkm.toml        # the only extra file you need
└── mylib/          # the code directory (import name = this folder's name)
    ├── __init__.py
    └── core.py
```

Minimal `pkm.toml`:

```toml
[package]
name = "mylib"
version = "1.0.0"

[dependencies]        # optional, exact versions only
# other = "1.0.0"
```

```bash
pkm keygen                    # one-time: generate the signing keypair (~/.pkm/keys/)
pkm build ./mylib             # -> mylib-1.0.0.pkm (signed)
pkm publish mylib-1.0.0.pkm   # store in the personal registry (~/.pkm/registry, auto git commit)
pkm registry push             # after configuring a remote, sync to a private GitHub repo
```

### Consumer: use it in any project

```bash
cd myapp
pkm install mylib@1.0.0       # verify signature -> extract to ./mylib/ -> done

# three ways to fetch a package (all signature-verified before install):
pkm install mylib                              # local registry first
pkm install ~/Downloads/mylib-1.0.0.pkm        # a standalone .pkm file (e.g. shared via chat/drive)
pkm install https://example.com/mylib-1.0.0.pkm  # straight from a URL

# remote registry fallback: subscribe to any cloud registry serving index.json
pkm source add hub https://yifeihuang-dev.github.io/pkm-registry
pkm source list
pkm install otherlib          # not found locally? fetched from "hub" automatically
```

```python
import mylib                  # just a normal folder, import away
```

Project layout after install:

```
myapp/
├── pkm.toml          # [dependencies] written automatically with exact versions
├── .pkm/installed.json   # install metadata
└── mylib/            # extracted as-is; committed to git along with the project
```

Once the vendored code is committed to git, **cloning on a new machine just works** — no reinstall needed.

## Command Reference

| Command | Description |
|---|---|
| `pkm keygen` | generate an Ed25519 signing keypair |
| `pkm keys` | list existing keys and fingerprints |
| `pkm build <dir>` | source directory -> `.pkm` (validates manifest + signs) |
| `pkm publish <file>` | publish to the local registry (records sha256/signature) |
| `pkm install [spec]` | install by name, name@version, .pkm file path, or http(s) URL; with no argument, installs everything in pkm.toml |
| `pkm upgrade [name]` | upgrade to the latest registry version |
| `pkm remove <name>` | remove (delete the directory + update the manifest) |
| `pkm list` | list packages installed in this project |
| `pkm source add/list/remove` | manage remote package sources (cloud registries serving index.json) |
| `pkm registry push/pull/path` | git sync for the registry |

## The `.pkm` Package Format

```
utils-1.0.0.pkm   (tar.gz)
├── pkm.toml      manifest
├── utils/        the code itself, packed as-is, extracted as-is
│   ├── __init__.py
│   ├── retry.py
│   └── net/http.py
└── SIGNATURE     "ed25519 <key_id> <signature>" (signs the digest of all other files)
```

## samples/ Examples

- `samples/utils`: a multi-file package with subdirectories
- `samples/logkit`: depends on `utils 1.0.0`, demonstrates automatic transitive dependencies
- `samples/toolslib`: depends on `utils 1.1.0`; installing it alongside logkit demonstrates the **conflict error**

Try it:

```bash
pkm keygen
pkm build samples/utils && pkm publish utils-1.0.0.pkm
pkm build samples/logkit && pkm publish logkit-1.0.0.pkm
mkdir /tmp/demo && cd /tmp/demo
pkm install logkit          # pulls utils automatically
python -c "import logkit; logkit.log('hello pkm')"
pkm list
```

## License

MIT
