"""
Daily git pull + incremental knowledge base update.

Pulls latest changes on secondbrain-repo/ clones, compares against last-synced
commit, and uses an LLM to update affected repo articles.

Usage:
    uv run python scripts/sync_repos.py                  # pull + update all tracked repos
    uv run python scripts/sync_repos.py --repo coplatform # single repo
    uv run python scripts/sync_repos.py --changed-only   # skip repos with no new commits
    uv run python scripts/sync_repos.py --no-pull        # skip git pull, just check for updates
    uv run python scripts/sync_repos.py --dry-run        # show what would be updated
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from config import (
    AGENTS_FILE,
    INDEX_FILE,
    LOG_FILE,
    REPOS_DIR,
    ROOT_DIR,
    get_tracked_repos,
    now_iso,
    repo_local_path,
)
from repo_utils import (
    classify_changes,
    ensure_clone,
    get_changed_files,
    get_current_commit,
    get_diff_content,
    get_diff_stat,
    git_pull,
)
from utils import load_state, read_wiki_index, save_state


MAX_DIFFS_IN_PROMPT = 20  # Cap to control cost
MAX_DIFF_CHARS = 3000  # Per-file diff size cap


UPDATE_PROMPT_TEMPLATE = """You are a knowledge base updater. A tracked repository has new changes.
Update the existing repo articles based on the git diff below.

## Repository: {repo_name}
Previous commit: {from_commit}
Current commit: {to_commit}

## Git Diff Summary
Files changed: {num_changed}
{diff_stat}

## Classified Changes
**High significance (models, routes, services):**
{high_changes}

**Medium significance (config, utils, middleware):**
{medium_changes}

## Detailed Diffs (significant files only)
{diff_content}

## Existing Repo Articles
{existing_articles}

## Schema Reference (article format)
{schema_excerpt}

## Current Knowledge Base Index
{wiki_index}

## Your Task

1. Read the diffs and understand what changed
2. Update existing articles that are affected by the changes using the Edit tool
3. Create new articles if the changes introduce new modules, models, or flows
4. If functionality was removed, update or note it in the relevant articles
5. Update the overview article's last_scanned_commit frontmatter to {to_commit}
6. Update knowledge/index.md for any new articles (add rows to existing table)
7. Append to knowledge/log.md:
   ## [{timestamp}] repo-sync | {repo_name}
   - Commits: {from_commit_short}..{to_commit_short}
   - Files changed: {num_changed}
   - Articles updated: [[list]]
   - Articles created: [[list]]

## Rules
- Only update articles that are actually affected by the diff
- Preserve existing content that isn't contradicted by the changes
- Use Edit tool to surgically update articles, not rewrite them entirely
- If a change is trivial (formatting, comments only), skip it
- If you need more context, use Read to check source files at {repo_path}

## File paths
- Repo articles directory: {repos_dir}/{repo_name}/
- Cross-repo connections: {repos_dir}/connections/
- Index: {index_file}
- Log: {log_file}
"""


async def update_repo_articles(
    repo_name: str,
    repo_path: Path,
    from_commit: str,
    to_commit: str,
    changed_files: list[dict],
    state: dict,
) -> float:
    """Generate targeted article updates based on git diff. Returns API cost."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    # Classify changes
    classified = classify_changes(changed_files)
    high = classified["high"]
    medium = classified["medium"]

    # Build diff content for significant files (capped)
    significant_files = high + medium
    diff_parts = []
    for f in significant_files[:MAX_DIFFS_IN_PROMPT]:
        diff = get_diff_content(repo_path, from_commit, to_commit, f["path"])
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... (truncated)"
        if diff:
            diff_parts.append(f"### {f['status']} {f['path']}\n```diff\n{diff}\n```")

    diff_content = "\n\n".join(diff_parts) if diff_parts else "(no significant diffs to show)"

    # Read existing repo articles
    articles_dir = REPOS_DIR / repo_name
    existing_parts = []
    if articles_dir.exists():
        for md_file in sorted(articles_dir.rglob("*.md")):
            rel = md_file.relative_to(REPOS_DIR.parent.parent)
            content = md_file.read_text(encoding="utf-8")
            existing_parts.append(f"### {rel}\n```markdown\n{content}\n```")

    existing_articles = "\n\n".join(existing_parts) if existing_parts else "(no existing articles)"

    # Schema excerpt (just the repo article formats section)
    schema = ""
    if AGENTS_FILE.exists():
        full_schema = AGENTS_FILE.read_text(encoding="utf-8")
        # Extract just the repo article sections
        start = full_schema.find("### Repo Overview Articles")
        end = full_schema.find("## Core Operations")
        if start >= 0 and end >= 0:
            schema = full_schema[start:end]
        else:
            schema = full_schema[:3000]

    diff_stat_text = get_diff_stat(repo_path, from_commit, to_commit)
    timestamp = now_iso()

    prompt = UPDATE_PROMPT_TEMPLATE.format(
        repo_name=repo_name,
        from_commit=from_commit,
        to_commit=to_commit,
        num_changed=len(changed_files),
        diff_stat=diff_stat_text,
        high_changes="\n".join(f"- [{f['status']}] {f['path']}" for f in high) or "(none)",
        medium_changes="\n".join(f"- [{f['status']}] {f['path']}" for f in medium) or "(none)",
        diff_content=diff_content,
        existing_articles=existing_articles,
        schema_excerpt=schema,
        wiki_index=read_wiki_index(),
        timestamp=timestamp,
        from_commit_short=from_commit[:8],
        to_commit_short=to_commit[:8],
        repo_path=str(repo_path),
        repos_dir=str(REPOS_DIR),
        index_file=str(INDEX_FILE),
        log_file=str(LOG_FILE),
    )

    response = ""
    cost = 0.0

    print(f"  Updating articles for {repo_name} ({len(significant_files)} significant files)...")
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(ROOT_DIR),
            system_prompt={"type": "preset", "preset": "claude_code"},
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=30,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response += block.text
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0

    # Update state
    if "repos" not in state:
        state["repos"] = {}
    if repo_name not in state["repos"]:
        state["repos"][repo_name] = {}
    state["repos"][repo_name]["last_synced_commit"] = to_commit
    state["repos"][repo_name]["synced_at"] = timestamp
    state["total_cost"] = state.get("total_cost", 0.0) + cost
    save_state(state)

    print(f"  Update complete. Cost: ${cost:.2f}")
    return cost


def sync_single_repo(
    repo_name: str,
    state: dict,
    do_pull: bool = True,
    dry_run: bool = False,
) -> float:
    """Sync a single repo: pull, diff, update articles. Returns cost."""
    repo_state = state.get("repos", {}).get(repo_name, {})
    last_commit = repo_state.get("last_synced_commit")

    if not last_commit:
        print(f"  {repo_name}: not yet scanned. Run scan_repo.py first.")
        return 0.0

    repo_path = repo_local_path(repo_name)
    if not repo_path.exists():
        print(f"  {repo_name}: clone not found at {repo_path}. Run scan_repo.py first.")
        return 0.0

    # Pull latest
    if do_pull:
        print(f"  Pulling {repo_name}...")
        success, output = git_pull(repo_path)
        if not success:
            print(f"  WARNING: git pull failed: {output}")
            return 0.0
        if "Already up to date" in output:
            print(f"  {repo_name}: already up to date")

    # Check for changes
    current_commit = get_current_commit(repo_path)
    if current_commit == last_commit:
        print(f"  {repo_name}: no new commits since last sync")
        return 0.0

    # Get changed files
    changed_files = get_changed_files(repo_path, last_commit, current_commit)
    if not changed_files:
        print(f"  {repo_name}: no file changes detected")
        # Still update the commit hash
        state.setdefault("repos", {}).setdefault(repo_name, {})
        state["repos"][repo_name]["last_synced_commit"] = current_commit
        state["repos"][repo_name]["synced_at"] = now_iso()
        save_state(state)
        return 0.0

    # Classify
    classified = classify_changes(changed_files)
    high_count = len(classified["high"])
    med_count = len(classified["medium"])
    low_count = len(classified["low"])

    print(f"  {repo_name}: {len(changed_files)} files changed "
          f"({high_count} high, {med_count} medium, {low_count} low significance)")
    print(f"  Commits: {last_commit[:8]}..{current_commit[:8]}")

    if high_count == 0 and med_count == 0:
        print(f"  {repo_name}: only low-significance changes, skipping article update")
        state.setdefault("repos", {}).setdefault(repo_name, {})
        state["repos"][repo_name]["last_synced_commit"] = current_commit
        state["repos"][repo_name]["synced_at"] = now_iso()
        save_state(state)
        return 0.0

    if dry_run:
        print(f"  [DRY RUN] Would update articles for {repo_name}")
        for f in (classified["high"] + classified["medium"])[:10]:
            print(f"    [{f['status']}] {f['path']}")
        return 0.0

    # Run LLM update
    cost = asyncio.run(update_repo_articles(
        repo_name, repo_path, last_commit, current_commit, changed_files, state
    ))
    return cost


def main():
    parser = argparse.ArgumentParser(description="Daily git pull + incremental KB update")
    parser.add_argument("--repo", type=str, help="Sync a specific repo")
    parser.add_argument("--changed-only", action="store_true", help="Skip repos with no new commits")
    parser.add_argument("--no-pull", action="store_true", help="Skip git pull")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated")
    args = parser.parse_args()

    state = load_state()
    tracked = get_tracked_repos()

    if args.repo:
        if args.repo not in tracked:
            print(f"Error: '{args.repo}' is not a tracked repo.")
            print(f"Available: {', '.join(tracked.keys())}")
            sys.exit(1)
        repos_to_sync = [args.repo]
    else:
        repos_to_sync = list(tracked.keys())

    print("\nRepo Knowledge Base Sync")
    print("=" * 40)
    total_cost = 0.0

    for repo_name in repos_to_sync:
        cost = sync_single_repo(
            repo_name,
            state,
            do_pull=not args.no_pull,
            dry_run=args.dry_run,
        )
        total_cost += cost

    if not args.dry_run and total_cost > 0:
        print(f"\nTotal sync cost: ${total_cost:.2f}")
        print(f"Cumulative total: ${state.get('total_cost', 0.0):.2f}")


if __name__ == "__main__":
    main()
