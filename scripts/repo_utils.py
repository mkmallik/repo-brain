"""Git helpers and utilities for repo knowledge base scanning and syncing."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from config import REPOS_DIR, SECONDBRAIN_DIR, get_tracked_repos

logger = logging.getLogger(__name__)

# ── File significance patterns ────────────────────────────────────────
# Used to classify changed files into high/medium/low significance
# so incremental updates only process meaningful changes.

SIGNIFICANCE_PATTERNS: dict[str, list[str]] = {
    "high": [
        r"models?[/\\]",
        r"schemas?[/\\]",
        r"routes?[/\\]",
        r"routers?[/\\]",
        r"controllers?[/\\]",
        r"services?[/\\]",
        r"api[/\\]",
        r"views?[/\\]",
        r"main\.py$",
        r"app\.py$",
        r"server\.(py|ts|js)$",
        r"index\.(ts|js)$",
        r"store[/\\]",
        r"stores?[/\\]",
    ],
    "medium": [
        r"config[/\\]",
        r"middleware[/\\]",
        r"utils?[/\\]",
        r"helpers?[/\\]",
        r"lib[/\\]",
        r"core[/\\]",
        r"types?[/\\]",
        r"interfaces?[/\\]",
        r"constants?\.(py|ts|js)$",
        r"settings?\.(py|ts|js)$",
        r"\.env\.example$",
        r"docker-compose",
        r"Dockerfile",
        r"requirements.*\.txt$",
        r"pyproject\.toml$",
        r"package\.json$",
    ],
    "low": [
        r"tests?[/\\]",
        r"__tests__[/\\]",
        r"spec[/\\]",
        r"\.test\.(py|ts|js|tsx)$",
        r"_test\.py$",
        r"\.spec\.(ts|js|tsx)$",
        r"README",
        r"CHANGELOG",
        r"\.md$",
        r"\.txt$",
        r"\.gitignore$",
        r"\.lock$",
        r"lock\.json$",
        r"node_modules[/\\]",
        r"__pycache__[/\\]",
        r"\.pyc$",
        r"\.map$",
        r"\.svg$",
        r"\.png$",
        r"\.jpg$",
        r"\.ico$",
    ],
}


# ── Git operations ────────────────────────────────────────────────────


def ensure_clone(repo_name: str, remote_url: str | None = None) -> Path:
    """Clone a repo into secondbrain-repo/ if missing, pull if it exists.

    Returns the local path to the clone.
    """
    if remote_url is None:
        remote_url = get_tracked_repos().get(repo_name)
    if not remote_url:
        raise ValueError(f"No remote URL for repo: {repo_name}")

    local_path = SECONDBRAIN_DIR / repo_name
    SECONDBRAIN_DIR.mkdir(parents=True, exist_ok=True)

    if local_path.exists() and (local_path / ".git").exists():
        logger.info("Clone exists at %s, pulling latest...", local_path)
        success, output = git_pull(local_path)
        if not success:
            logger.warning("git pull failed for %s: %s", repo_name, output)
    else:
        logger.info("Cloning %s from %s...", repo_name, remote_url)
        result = subprocess.run(
            ["git", "clone", remote_url, str(local_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed for {repo_name}: {result.stderr}")
        logger.info("Cloned %s successfully", repo_name)

    return local_path


def get_current_commit(repo_path: Path) -> str:
    """Get the HEAD commit SHA for a repo."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {result.stderr}")
    return result.stdout.strip()


def get_current_branch(repo_path: Path) -> str:
    """Get the current branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def git_pull(repo_path: Path) -> tuple[bool, str]:
    """Run git pull on a repo. Returns (success, output)."""
    result = subprocess.run(
        ["git", "pull"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode == 0, output


def get_changed_files(
    repo_path: Path, from_commit: str, to_commit: str
) -> list[dict]:
    """Get list of changed files between two commits.

    Returns list of dicts with keys: status ('A'/'M'/'D'/'R'), path, old_path (for renames).
    """
    result = subprocess.run(
        ["git", "diff", "--name-status", from_commit, to_commit],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    files = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][0]  # First char: A, M, D, R, C
        path = parts[-1]
        old_path = parts[1] if len(parts) > 2 else None
        files.append({"status": status, "path": path, "old_path": old_path})

    return files


def get_diff_content(
    repo_path: Path, from_commit: str, to_commit: str, file_path: str
) -> str:
    """Get the unified diff for a specific file between two commits."""
    result = subprocess.run(
        ["git", "diff", from_commit, to_commit, "--", file_path],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def get_diff_stat(repo_path: Path, from_commit: str, to_commit: str) -> str:
    """Get a short diffstat summary between two commits."""
    result = subprocess.run(
        ["git", "diff", "--stat", from_commit, to_commit],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def get_recent_commits(repo_path: Path, since: str = "today") -> str:
    """Get recent commit log (oneline format)."""
    result = subprocess.run(
        ["git", "log", "--oneline", f"--since={since}", "--no-merges"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def get_uncommitted_stat(repo_path: Path) -> str:
    """Get diffstat for uncommitted changes in a working copy."""
    result = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


# ── Change classification ─────────────────────────────────────────────


def classify_changes(changed_files: list[dict]) -> dict[str, list[dict]]:
    """Classify changed files by significance: high, medium, low, skip.

    Files matching no pattern default to 'medium'.
    """
    result: dict[str, list[dict]] = {"high": [], "medium": [], "low": [], "skip": []}

    for f in changed_files:
        path = f["path"]
        classified = False
        for level in ["high", "low"]:  # Check high and low first
            for pattern in SIGNIFICANCE_PATTERNS[level]:
                if re.search(pattern, path, re.IGNORECASE):
                    result[level].append(f)
                    classified = True
                    break
            if classified:
                break
        if not classified:
            # Check medium patterns
            for pattern in SIGNIFICANCE_PATTERNS["medium"]:
                if re.search(pattern, path, re.IGNORECASE):
                    result["medium"].append(f)
                    classified = True
                    break
        if not classified:
            result["medium"].append(f)  # Default to medium

    return result


# ── Path helpers ──────────────────────────────────────────────────────


def repo_articles_dir(repo_name: str) -> Path:
    """Ensure and return knowledge/repos/{repo_name}/ path."""
    d = REPOS_DIR / repo_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def repo_connections_dir() -> Path:
    """Ensure and return knowledge/repos/connections/ path."""
    d = REPOS_DIR / "connections"
    d.mkdir(parents=True, exist_ok=True)
    return d
