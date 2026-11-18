# Publishing to the pkm Registry

Anyone can publish into this registry — via a Pull Request. The registry is
just git + static files, so the whole flow is standard GitHub.

Trust is NOT granted by merging a PR. Merging only makes a package *visible*
in the hub. Every install re-verifies the package's Ed25519 signature locally
against the signer's public key, and every new key fingerprint requires an
explicit trust decision (TOFU) on the installer's machine.

## How to publish your package

1. **Fork** this repository.

2. **Create your own signing key** (one identity per person, stays on your machine):

   ```bash
   pkm keygen
   ```

   Note the `key_id` it prints — this is your publisher identity.

3. **Write your package**: a directory containing a `pkm.toml` manifest and a
   code folder named exactly like your package:

   ```
   mylib/
   ├── pkm.toml           # [package] name = "mylib", version = "1.0.0"
   └── mylib/
       ├── __init__.py
       └── ...
   ```

4. **Build and publish** (publishes into your local registry):

   ```bash
   pkm build ./mylib
   pkm publish mylib-1.0.0.pkm
   ```

5. **Copy the artifacts into your fork**: the `.pkm` file under
   `packages/mylib/`, plus the matching `index.json` entry:

   ```json
   "mylib": {
     "1.0.0": {
       "file": "mylib-1.0.0.pkm",
       "sha256": "<printed by pkm publish>",
       "pubkey": "<your public key, base64>",
       "key_id": "<your key_id>",
       "published_at": "<timestamp>"
     }
   }
   ```

6. **Open a Pull Request** against `main`.

## What the maintainer reviews

| Check | Why |
|---|---|
| Source diff (every file in the package) | The `.pkm` is plain tar.gz — review exactly what will run |
| Name collisions / lookalikes | Package names are first-come, first-served; typosquatting is rejected |
| Signature fields | `key_id` + `pubkey` must match the signature inside the `.pkm` |
| Single file < 100 MB, no datasets | GitHub hard limit; keep packages code-only |

Reviewers can verify a PR's package without installing anything:

```bash
pkm publish path/to/package-1.0.0.pkm --verify-only
```

## Rules

- **Versions are immutable.** A PR that changes the `sha256` of an existing
  package version (same-version poisoning) is rejected outright. Publish a
  new version instead.
- **Names are immutable once merged.** Renames happen via a new package, and
  the old name is retired, not reused.
- **No secrets, no datasets, no compiled binaries** without prior agreement.

## After merge

Anyone on any machine can then use your package:

```bash
pkm source add hub https://yifeihuang-dev.github.io/pkm-registry
pkm install mylib            # or stream it, code never lands on disk:
pkm use mylib
python -m package_manager.cloud -c "import mylib"
```

The installer's TOFU store will show your `key_id` on first use — that's
your signature being checked, not the hub's word for it.
