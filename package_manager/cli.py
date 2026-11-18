"""pkm command-line entry point."""

import argparse
from pathlib import Path

from . import cloud, pkging, project, registry, signing, sources
from .errors import PkmError


def _cmd_keygen(args):
    signing.keygen()


def _cmd_keys(args):
    signing.keys_list()


def _cmd_build(args):
    out, sha = pkging.build(Path(args.dir).resolve(), out=args.out)
    print(f"✓ built: {out}")
    print(f"  sha256: {sha}")


def _cmd_publish(args):
    if args.verify_only:
        project.verify_only(args.file)
        return
    registry.publish(Path(args.file).resolve())


def _cmd_install(args):
    project.install(args.spec, force=args.force)


def _cmd_upgrade(args):
    project.upgrade(args.name)


def _cmd_remove(args):
    project.remove(args.name)


def _cmd_list(args):
    project.list_installed()


def _cmd_registry(args):
    if args.op == "path":
        print(registry.REGISTRY_DIR)
    elif args.op == "push":
        registry.git_push()
    elif args.op == "pull":
        registry.git_pull()


def _cmd_search(args):
    registry.search(args.query)


def _cmd_trust(args):
    if args.op == "list":
        signing.trust_list()
    elif args.op == "remove":
        if not args.key_id:
            raise PkmError("usage: pkm trust remove <key_id>")
        signing.trust_remove(args.key_id)


def _cmd_use(args):
    cloud.use(args.spec, latest=args.latest)


def _cmd_source(args):
    if args.op == "add":
        if not args.name or not args.url:
            raise PkmError("usage: pkm source add <name> <url>")
        sources.add(args.name, args.url)
    elif args.op == "list":
        sources.list_sources()
    elif args.op == "remove":
        if not args.name:
            raise PkmError("usage: pkm source remove <name>")
        sources.remove(args.name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pkm", description="pkm — a personal vendoring-style Python package manager"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen", help="generate an Ed25519 signing keypair")
    p.set_defaults(func=_cmd_keygen)

    p = sub.add_parser("keys", help="list existing signing keys")
    p.set_defaults(func=_cmd_keys)

    p = sub.add_parser("build", help="source directory -> <name>-<version>.pkm (signed)")
    p.add_argument("dir", help="source directory containing pkm.toml")
    p.add_argument("-o", "--out", default=None, help="output .pkm file path")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("publish", help="publish a .pkm to the local registry")
    p.add_argument("file", help="path to a .pkm file")
    p.add_argument("--verify-only", action="store_true",
                   help="verify and report without storing (for reviewing community PRs)")
    p.set_defaults(func=_cmd_publish)

    p = sub.add_parser("search", help="search packages by name across the local registry and remote sources")
    p.add_argument("query", help="name substring to look for")
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("trust", help="manage trusted publisher keys (TOFU store): list | remove")
    p.add_argument("op", choices=["list", "remove"])
    p.add_argument("key_id", nargs="?", default=None, help="key_id to revoke (for remove)")
    p.set_defaults(func=_cmd_trust)

    p = sub.add_parser("install", help="install a package: verify signature -> extract into project root -> importable")
    p.add_argument("spec", nargs="?", default=None,
                   help="name, name@version, path to a .pkm file, or an http(s) URL; omit to install everything in pkm.toml")
    p.add_argument("--force", action="store_true", help="overwrite existing directories of the same name")
    p.set_defaults(func=_cmd_install)

    p = sub.add_parser("use", help="cloud mode: stream a package into memory at run time (code never lands on disk)")
    p.add_argument("spec", help="name or name@version; pairs with: python -m package_manager.cloud")
    p.add_argument("--latest", action="store_true", help="follow the newest version on every run (default: pin)")
    p.set_defaults(func=_cmd_use)

    p = sub.add_parser("source", help="manage remote package sources (cloud registries): add | list | remove")
    p.add_argument("op", choices=["add", "list", "remove"])
    p.add_argument("name", nargs="?", default=None, help="source name (for add/remove)")
    p.add_argument("url", nargs="?", default=None, help="source URL serving index.json (for add)")
    p.set_defaults(func=_cmd_source)

    p = sub.add_parser("upgrade", help="upgrade to the latest registry version")
    p.add_argument("name", nargs="?", default=None, help="package name; omit to upgrade all")
    p.set_defaults(func=_cmd_upgrade)

    p = sub.add_parser("remove", help="remove an installed package")
    p.add_argument("name")
    p.set_defaults(func=_cmd_remove)

    p = sub.add_parser("list", help="list packages installed in this project")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("registry", help="registry management: push | pull | path")
    p.add_argument("op", choices=["push", "pull", "path"])
    p.set_defaults(func=_cmd_registry)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except PkmError as e:
        print(f"✗ {e}")
        return 1
    return 0
