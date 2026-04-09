"""
Daily maintenance - run repo sync + compile + dev activity capture.

Designed to be called by cron or launchd as a reliable daily trigger,
independent of whether Claude sessions are open or closed.

Usage:
    uv run python scripts/daily_maintenance.py          # run all maintenance
    uv run python scripts/daily_maintenance.py --dry-run # show what would run

Cron example (daily at 6 PM):
    0 18 * * * /path/to/uv run --directory /path/to/repo-brain python scripts/daily_maintenance.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import ROOT_DIR, DAILY_DIR, SCRIPTS_DIR, UV_BIN, get_working_copies

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def capture_dev_activity() -> int:
    """Capture today's commits and uncommitted changes from working copies."""
    now = datetime.now(timezone.utc).astimezone()
    working_copies = get_working_copies()

    if not working_copies:
        logging.info("No working copies configured, skipping dev activity")
        return 0

    activity_parts = []
    for repo_name, repo_path in working_copies.items():
        if not repo_path.exists() or not (repo_path / ".git").exists():
            continue
        repo_activity = []

        result = subprocess.run(
            ["git", "log", "--oneline", "--since=today", "--no-merges"],
            cwd=str(repo_path), capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            repo_activity.append(f"**Commits today:**\n{result.stdout.strip()}")

        result = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(repo_path), capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            repo_activity.append(f"**Uncommitted changes:**\n{result.stdout.strip()}")

        if repo_activity:
            activity_parts.append(f"#### {repo_name}\n\n" + "\n\n".join(repo_activity))

    if activity_parts:
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        log_path = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"
        content = "\n\n### Dev Activity\n\n" + "\n\n---\n\n".join(activity_parts) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(content)
        logging.info("Captured dev activity for %d repos", len(activity_parts))
    else:
        logging.info("No dev activity today")

    return len(activity_parts)


def run_sync() -> bool:
    """Run sync_repos.py synchronously."""
    sync_script = SCRIPTS_DIR / "sync_repos.py"
    if not sync_script.exists():
        logging.warning("sync_repos.py not found, skipping")
        return False

    logging.info("Running repo sync...")
    result = subprocess.run(
        [UV_BIN, "run", "--directory", str(ROOT_DIR), "python", str(sync_script)],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logging.info("Repo sync complete")
        if result.stdout.strip():
            logging.info(result.stdout.strip()[-500:])
    else:
        logging.error("Repo sync failed: %s", result.stderr[-500:] if result.stderr else "unknown")
    return result.returncode == 0


def run_compile() -> bool:
    """Run compile.py synchronously."""
    compile_script = SCRIPTS_DIR / "compile.py"
    if not compile_script.exists():
        logging.warning("compile.py not found, skipping")
        return False

    now = datetime.now(timezone.utc).astimezone()
    today_log = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    if not today_log.exists():
        logging.info("No daily log for today, skipping compile")
        return False

    logging.info("Running daily log compile...")
    result = subprocess.run(
        [UV_BIN, "run", "--directory", str(ROOT_DIR), "python", str(compile_script)],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logging.info("Compile complete")
    else:
        logging.error("Compile failed: %s", result.stderr[-500:] if result.stderr else "unknown")
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Run daily knowledge base maintenance")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run")
    args = parser.parse_args()

    logging.info("=" * 40)
    logging.info("Daily maintenance started")

    if args.dry_run:
        logging.info("[DRY RUN] Would run: dev activity capture, repo sync, daily compile")
        return

    # 1. Dev activity capture (fast, just git commands)
    capture_dev_activity()

    # 2. Repo sync (pulls repos, LLM updates articles if changes found)
    run_sync()

    # 3. Compile daily log into knowledge articles
    run_compile()

    # Write marker
    marker_file = SCRIPTS_DIR / "last-daily-maintenance.json"
    now = datetime.now(timezone.utc).astimezone()
    marker_file.write_text(
        json.dumps({"date": now.strftime("%Y-%m-%d"), "ran": ["cron"]}),
        encoding="utf-8",
    )

    logging.info("Daily maintenance complete")


if __name__ == "__main__":
    main()
