"""Path constants and configuration for the personal knowledge base."""

import json
from pathlib import Path
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT_DIR / "daily"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
QA_DIR = KNOWLEDGE_DIR / "qa"
REPORTS_DIR = ROOT_DIR / "reports"
SCRIPTS_DIR = ROOT_DIR / "scripts"
HOOKS_DIR = ROOT_DIR / "hooks"
AGENTS_FILE = ROOT_DIR / "AGENTS.md"

REPOS_DIR = KNOWLEDGE_DIR / "repos"
SECONDBRAIN_DIR = ROOT_DIR / "secondbrain-repo"
REPOS_CONFIG_FILE = ROOT_DIR / "repos.json"

INDEX_FILE = KNOWLEDGE_DIR / "index.md"
LOG_FILE = KNOWLEDGE_DIR / "log.md"
STATE_FILE = SCRIPTS_DIR / "state.json"


# ── Repo configuration (loaded from repos.json) ──────────────────────

def _load_repos_config() -> dict:
    """Load repo configuration from repos.json."""
    if REPOS_CONFIG_FILE.exists():
        try:
            return json.loads(REPOS_CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"repos": {}}


def get_tracked_repos() -> dict[str, str]:
    """Return {name: remote_url} for all tracked repos."""
    config = _load_repos_config()
    return {
        name: info["remote"]
        for name, info in config.get("repos", {}).items()
        if "remote" in info
    }


def get_working_copies() -> dict[str, Path]:
    """Return {name: local_path} for repos with a working_copy configured."""
    config = _load_repos_config()
    return {
        name: Path(info["working_copy"])
        for name, info in config.get("repos", {}).items()
        if info.get("working_copy")
    }


def repo_local_path(repo_name: str) -> Path:
    """Return the clean clone path for a tracked repo."""
    return SECONDBRAIN_DIR / repo_name


# ── Timezone ───────────────────────────────────────────────────────────
TIMEZONE = "America/Chicago"


def now_iso() -> str:
    """Current time in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    """Current date in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
