"""
SessionStart hook - injects knowledge base context into every conversation.

This is the "context injection" layer. When Claude Code starts a session,
this hook reads the knowledge base index and recent daily log, then injects
them as additional context so Claude always "remembers" what it has learned.

Also triggers end-of-day maintenance (repo sync, compile, dev activity capture)
if it hasn't run today. This is essential for Claude Desktop where sessions
are long-running and SessionEnd rarely fires.

Configure in .claude/settings.json:
{
    "hooks": {
        "SessionStart": [{
            "matcher": "",
            "command": "uv run python hooks/session-start.py"
        }]
    }
}
"""

import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Paths relative to project root
ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
DAILY_DIR = ROOT / "daily"
REPOS_DIR = KNOWLEDGE_DIR / "repos"
SCRIPTS_DIR = ROOT / "scripts"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"
STATE_FILE = SCRIPTS_DIR / "state.json"

MAX_CONTEXT_CHARS = 40_000
MAX_LOG_LINES = 30
EOD_HOUR = 18  # 6 PM — trigger daily maintenance after this hour

logging.basicConfig(
    filename=str(SCRIPTS_DIR / "session-start.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [session-start] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_recent_log() -> str:
    """Read the most recent daily log (today or yesterday)."""
    today = datetime.now(timezone.utc).astimezone()

    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            # Return last N lines to keep context small
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return "\n".join(recent)

    return "(no recent daily log)"


def get_repo_summary() -> str:
    """Build a brief summary of scanned repos and their last-updated dates."""
    if not STATE_FILE.exists():
        return ""

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    repos = state.get("repos", {})
    if not repos:
        return ""

    lines = ["Scanned repositories with deep architecture knowledge available:"]
    for name, info in sorted(repos.items()):
        scanned = info.get("scanned_at", "unknown")[:10]
        synced = info.get("synced_at", "unknown")[:10]
        articles = info.get("articles_created", 0)
        commit = info.get("last_synced_commit", "?")[:8]
        lines.append(f"- **{name}**: {articles} articles, commit {commit}, synced {synced}")

    lines.append("")
    lines.append("Query repo knowledge with: `uv run python scripts/query.py \"question about repo\"`")
    return "\n".join(lines)


def build_context() -> str:
    """Assemble the context to inject into the conversation."""
    parts = []

    # Today's date
    today = datetime.now(timezone.utc).astimezone()
    parts.append(f"## Today\n{today.strftime('%A, %B %d, %Y')}")

    # Repo knowledge summary (concise, always fits — put first for visibility)
    repo_summary = get_repo_summary()
    if repo_summary:
        parts.append(f"## Repo Knowledge\n\n{repo_summary}")

    # Knowledge base index (the core retrieval mechanism)
    if INDEX_FILE.exists():
        index_content = INDEX_FILE.read_text(encoding="utf-8")
        parts.append(f"## Knowledge Base Index\n\n{index_content}")
    else:
        parts.append("## Knowledge Base Index\n\n(empty - no articles compiled yet)")

    # Recent daily log
    recent_log = get_recent_log()
    parts.append(f"## Recent Daily Log\n\n{recent_log}")

    # Instructions for using the knowledge base
    kb_path = str(KNOWLEDGE_DIR)
    parts.append(
        f"## How to Use This Knowledge Base\n\n"
        f"The knowledge base lives at `{kb_path}/`. "
        f"To answer questions about repo architecture, read the relevant article files directly. "
        f"For example, to learn about coplatform's architecture, read `{kb_path}/repos/coplatform/overview.md`. "
        f"Use `uv run python scripts/query.py \"question\"` for index-guided search."
    )

    context = "\n\n---\n\n".join(parts)

    # Truncate if too long
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"

    return context


def maybe_run_daily_maintenance() -> None:
    """Trigger end-of-day maintenance if it hasn't run today.

    In Claude Desktop, sessions are long-running and SessionEnd rarely fires,
    so we check on SessionStart whether today's maintenance has been done.
    Spawns background processes for: repo sync, daily log compile, dev activity.
    """
    now = datetime.now(timezone.utc).astimezone()
    if now.hour < EOD_HOUR:
        return

    # Check if we already ran maintenance today
    marker_file = SCRIPTS_DIR / "last-daily-maintenance.json"
    if marker_file.exists():
        try:
            marker = json.loads(marker_file.read_text(encoding="utf-8"))
            last_date = marker.get("date", "")
            if last_date == now.strftime("%Y-%m-%d"):
                return  # Already ran today
        except (json.JSONDecodeError, OSError):
            pass

    # Resolve uv path
    uv_bin = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")

    spawned = []

    # 1. Repo sync (pull + incremental article updates)
    sync_script = SCRIPTS_DIR / "sync_repos.py"
    if sync_script.exists():
        try:
            log_handle = open(str(SCRIPTS_DIR / "sync.log"), "a")
            subprocess.Popen(
                [uv_bin, "run", "--directory", str(ROOT), "python", str(sync_script)],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT),
                start_new_session=True,
            )
            spawned.append("sync_repos")
        except Exception as e:
            logging.error("Failed to spawn sync_repos.py: %s", e)

    # 2. Compile daily log into articles
    compile_script = SCRIPTS_DIR / "compile.py"
    if compile_script.exists():
        today_log = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"
        if today_log.exists():
            try:
                log_handle = open(str(SCRIPTS_DIR / "compile.log"), "a")
                subprocess.Popen(
                    [uv_bin, "run", "--directory", str(ROOT), "python", str(compile_script)],
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    cwd=str(ROOT),
                    start_new_session=True,
                )
                spawned.append("compile")
            except Exception as e:
                logging.error("Failed to spawn compile.py: %s", e)

    # 3. Dev activity capture (working copy commits + uncommitted changes)
    # This is lightweight (just git commands), so run inline
    try:
        repos_config_file = ROOT / "repos.json"
        if repos_config_file.exists():
            rc = json.loads(repos_config_file.read_text(encoding="utf-8"))
            working_copies = {
                name: Path(info["working_copy"])
                for name, info in rc.get("repos", {}).items()
                if info.get("working_copy")
            }

            activity_parts = []
            for repo_name, repo_path in working_copies.items():
                if not repo_path.exists() or not (repo_path / ".git").exists():
                    continue
                repo_activity = []

                # Today's commits
                result = subprocess.run(
                    ["git", "log", "--oneline", "--since=today", "--no-merges"],
                    cwd=str(repo_path), capture_output=True, text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    repo_activity.append(f"**Commits today:**\n{result.stdout.strip()}")

                # Uncommitted changes
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
                spawned.append("dev_activity")
    except Exception as e:
        logging.error("Dev activity capture failed: %s", e)

    # Write marker so we don't re-trigger today
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(
        json.dumps({"date": now.strftime("%Y-%m-%d"), "ran": spawned}),
        encoding="utf-8",
    )
    logging.info("Daily maintenance triggered: %s", ", ".join(spawned) or "nothing to do")


def main():
    # Trigger daily maintenance if needed (background, non-blocking)
    try:
        maybe_run_daily_maintenance()
    except Exception as e:
        logging.error("Daily maintenance check failed: %s", e)

    context = build_context()

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
