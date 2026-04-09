"""
SessionStart hook - injects knowledge base context into every conversation.

This is the "context injection" layer. When Claude Code starts a session,
this hook reads the knowledge base index and recent daily log, then injects
them as additional context so Claude always "remembers" what it has learned.

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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Paths relative to project root
ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
DAILY_DIR = ROOT / "daily"
REPOS_DIR = KNOWLEDGE_DIR / "repos"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"
STATE_FILE = ROOT / "scripts" / "state.json"

MAX_CONTEXT_CHARS = 40_000
MAX_LOG_LINES = 30


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


def main():
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
