"""pywebfw command line: scaffold new projects.

    pywebfw new myproject [--dir PATH]
    pywebfw version

(Also runnable as `python -m pywebfw ...`.)
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

import pywebfw
from pywebfw.scaffold import render_project_files

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _cmd_new(args: argparse.Namespace) -> int:
    name = args.name
    if not _NAME_RE.match(name):
        print(f"error: '{name}' is not a valid package name "
              "(lowercase letters, digits, underscores; must start with a letter)")
        return 1
    target = Path(args.dir) / name
    if target.exists():
        print(f"error: {target} already exists")
        return 1

    secret_key = secrets.token_urlsafe(48)
    files = render_project_files(name, secret_key)
    for relative_path, content in files.items():
        path = target / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    print(f"Created project '{name}' at {target}  ({len(files)} files)")
    print()
    print("Next steps:")
    print(f"  cd {target}")
    print("  python -m venv .venv && .venv/Scripts/pip install -r requirements.txt")
    print("  # install the framework: pip install pywebfw  (or -e <framework repo>)")
    print("  python run.py")
    print()
    print("Public site: http://127.0.0.1:8000/welcome — Admin: /admin")
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"pywebfw {pywebfw.__version__}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pywebfw",
                                     description="pywebfw project scaffolding")
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="create a new project")
    new_parser.add_argument("name", help="project package name (e.g. mysite)")
    new_parser.add_argument("--dir", default=".", help="parent directory (default: .)")
    new_parser.set_defaults(func=_cmd_new)

    version_parser = sub.add_parser("version", help="print the framework version")
    version_parser.set_defaults(func=_cmd_version)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
