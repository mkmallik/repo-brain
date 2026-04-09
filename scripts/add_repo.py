"""
Add a git repository to the knowledge base for tracking.

Usage:
    uv run python scripts/add_repo.py <remote_url>
    uv run python scripts/add_repo.py <remote_url> --name my-repo
    uv run python scripts/add_repo.py <remote_url> --working-copy /path/to/local/repo
    uv run python scripts/add_repo.py --list
    uv run python scripts/add_repo.py --remove my-repo

Examples:
    uv run python scripts/add_repo.py https://github.com/user/my-app.git
    uv run python scripts/add_repo.py git@github.com:user/my-app.git --working-copy ~/projects/my-app
    uv run python scripts/add_repo.py https://github.com/user/api.git --name backend-api
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from config import REPOS_CONFIG_FILE, ROOT_DIR


def load_config() -> dict:
    if REPOS_CONFIG_FILE.exists():
        return json.loads(REPOS_CONFIG_FILE.read_text(encoding="utf-8"))
    return {"repos": {}}


def save_config(config: dict) -> None:
    REPOS_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def infer_name(remote_url: str) -> str:
    """Infer a short repo name from a git remote URL."""
    # Handle SSH: git@github.com:user/repo.git
    # Handle HTTPS: https://github.com/user/repo.git
    match = re.search(r"[/:]([^/:]+?)(?:\.git)?$", remote_url)
    if match:
        return match.group(1).lower()
    return remote_url.rsplit("/", 1)[-1].replace(".git", "").lower()


def add_repo(remote_url: str, name: str | None = None, working_copy: str | None = None) -> None:
    config = load_config()

    if name is None:
        name = infer_name(remote_url)

    if name in config.get("repos", {}):
        print(f"Repo '{name}' already exists. Use --remove first to re-add.")
        sys.exit(1)

    entry: dict = {"remote": remote_url}
    if working_copy:
        wc = Path(working_copy).expanduser().resolve()
        if not wc.exists():
            print(f"Warning: working copy path does not exist: {wc}")
        entry["working_copy"] = str(wc)

    config.setdefault("repos", {})[name] = entry
    save_config(config)

    print(f"Added repo: {name}")
    print(f"  Remote: {remote_url}")
    if working_copy:
        print(f"  Working copy: {entry['working_copy']}")
    print(f"\nNext: run 'uv run python scripts/scan_repo.py --repo {name}' to build knowledge base")


def remove_repo(name: str) -> None:
    config = load_config()
    if name not in config.get("repos", {}):
        print(f"Repo '{name}' not found.")
        sys.exit(1)

    del config["repos"][name]
    save_config(config)
    print(f"Removed repo '{name}' from tracking.")
    print(f"Note: existing articles in knowledge/repos/{name}/ are preserved. Delete manually if needed.")


def list_repos() -> None:
    config = load_config()
    repos = config.get("repos", {})

    if not repos:
        print("No repos configured yet.")
        print("\nAdd one with:")
        print("  uv run python scripts/add_repo.py https://github.com/user/repo.git")
        return

    print(f"\nTracked repos ({len(repos)}):")
    print("-" * 60)
    for name, info in repos.items():
        print(f"  {name}")
        print(f"    Remote: {info.get('remote', '(none)')}")
        if info.get("working_copy"):
            print(f"    Working copy: {info['working_copy']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Manage tracked repositories for the knowledge base",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run python scripts/add_repo.py https://github.com/user/my-app.git
  uv run python scripts/add_repo.py git@github.com:user/api.git --name backend
  uv run python scripts/add_repo.py https://github.com/user/app.git --working-copy ~/dev/app
  uv run python scripts/add_repo.py --list
  uv run python scripts/add_repo.py --remove old-repo""",
    )
    parser.add_argument("remote_url", nargs="?", help="Git remote URL to add")
    parser.add_argument("--name", type=str, help="Short name for the repo (auto-inferred from URL if omitted)")
    parser.add_argument("--working-copy", type=str, help="Path to your local working copy (for dev activity capture)")
    parser.add_argument("--list", action="store_true", help="List all tracked repos")
    parser.add_argument("--remove", type=str, metavar="NAME", help="Remove a repo from tracking")

    args = parser.parse_args()

    if args.list:
        list_repos()
    elif args.remove:
        remove_repo(args.remove)
    elif args.remote_url:
        add_repo(args.remote_url, name=args.name, working_copy=args.working_copy)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
