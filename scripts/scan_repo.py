"""
One-time interactive deep repo scanner.

Clones repos into secondbrain-repo/, then uses a two-phase LLM approach:
  Phase 1 (Discovery): Explores the repo with Read/Glob/Grep, outputs JSON manifest.
  Phase 2 (Writing):   Creates structured knowledge articles from the manifest.

Usage:
    uv run python scripts/scan_repo.py                        # interactive, asks per repo
    uv run python scripts/scan_repo.py --repo my-app          # scan a specific repo
    uv run python scripts/scan_repo.py --repo my-app --force  # rescan even if done
    uv run python scripts/scan_repo.py --dry-run              # show what would be scanned
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
    today_iso,
)
from repo_utils import ensure_clone, get_current_commit, repo_articles_dir
from utils import load_state, read_wiki_index, save_state, update_state


# ── Phase 1: Discovery ───────────────────────────────────────────────

PHASE1_PROMPT_TEMPLATE = """You are a codebase analyst. Your job is to deeply explore a repository
and produce a structured analysis.

## Repository
Name: {repo_name}
Path: {repo_path}

## Your Task

Explore this repository thoroughly using Read, Glob, and Grep tools. Build a
complete understanding of:

1. **Architecture** - Overall structure, frameworks, patterns used
2. **Entry points** - Main files, server startup, CLI entry points
3. **Modules/Services** - Major logical units of the codebase
4. **API Surface** - HTTP endpoints, GraphQL schemas, CLI commands, exported functions
5. **Data Models** - Database models, schemas, DTOs, type definitions
6. **Key Flows** - Important end-to-end workflows (e.g., user auth, data processing pipelines)
7. **External Dependencies** - APIs called, services integrated with
8. **Configuration** - Environment variables, config files, feature flags

## Exploration Strategy

1. Start with README, package.json/pyproject.toml, and top-level directory listing
2. Read entry point files (main.py, app.py, index.ts, server.ts, etc.)
3. Explore each major directory with Glob
4. Read key model/schema files
5. Read route/controller files
6. Read service/business logic files
7. Note cross-repo dependencies (other tracked repos this depends on)

## Output Format

Return a JSON object (inside a ```json code fence) with this structure:
{{
  "overview": {{
    "description": "One paragraph describing the repo purpose and architecture",
    "tech_stack": ["Python 3.12", "FastAPI", "PostgreSQL", ...],
    "framework": "FastAPI",
    "architecture_pattern": "Layered MVC / Service-oriented / etc.",
    "entry_points": ["app/main.py", ...]
  }},
  "modules": [
    {{
      "name": "module-name",
      "path": "app/modules/name",
      "purpose": "What this module does",
      "key_files": ["file1.py", "file2.py"],
      "public_api": ["function_name(args) -> return", "ClassName"]
    }}
  ],
  "data_models": [
    {{
      "name": "ModelName",
      "path": "app/models/model.py",
      "fields_summary": "Brief description of key fields",
      "relationships": ["ForeignKey to OtherModel", ...]
    }}
  ],
  "api_endpoints": [
    {{
      "group": "group-name",
      "base_path": "/api/v1/resource",
      "router_file": "app/routers/resource.py",
      "endpoints": [
        {{"method": "GET", "path": "/api/v1/resource", "purpose": "List resources"}},
        {{"method": "POST", "path": "/api/v1/resource", "purpose": "Create resource"}}
      ]
    }}
  ],
  "key_flows": [
    {{
      "name": "flow-name",
      "description": "What this flow does end-to-end",
      "trigger": "What initiates it",
      "steps": ["Step 1: ...", "Step 2: ..."],
      "services_involved": ["module-a", "module-b"]
    }}
  ],
  "cross_repo_connections": [
    {{
      "this_repo_component": "module or service in this repo",
      "other_repo": "repo-name",
      "other_component": "module or service in the other repo",
      "relationship": "Consumes API / Shares DB / Publishes to queue / etc."
    }}
  ],
  "configuration": {{
    "env_vars": ["KEY=description", ...],
    "config_files": ["config/settings.py", ...],
    "feature_flags": ["FLAG_NAME - description", ...]
  }}
}}

Be thorough. Read actual source files, not just directory listings. If the repo is large,
focus on the most important modules (services, routes, models) rather than utilities and tests.
"""


# ── Phase 2: Article Writing ─────────────────────────────────────────

PHASE2_PROMPT_TEMPLATE = """You are a knowledge compiler for a codebase wiki. Using the analysis below,
create structured knowledge articles for the repository.

## Schema Reference
{schema}

## Repository Analysis (from Phase 1)
```json
{phase1_output}
```

## Repository Path (for reading source files if needed)
{repo_path}

## Current Knowledge Base Index
{wiki_index}

## Your Task

Create the following articles under knowledge/repos/{repo_name}/:

1. **overview.md** - Architecture overview of the entire repo
   - Frontmatter: title, repo, tags, last_scanned_commit, created, updated
   - Sections: Architecture Overview, Technology Stack, Directory Structure,
     Key Modules (with [[wikilinks]] to module articles), External Dependencies

2. **module-{{name}}.md** - One per major module identified in the analysis
   - Frontmatter: title, repo, module_path, tags, created, updated
   - Sections: Purpose, Public API, Internal Architecture, Key Functions,
     Data Models Used, Dependencies

3. **model-{{name}}.md** - One per significant data model
   - Frontmatter: title, repo, model_path, tags, created, updated
   - Sections: Schema/Fields, Relationships, Validation Rules, Usage Patterns

4. **api-{{name}}.md** - One per API endpoint group
   - Frontmatter: title, repo, base_path, tags, created, updated
   - Sections: Endpoints (table), Authentication, Request/Response, Error Handling

5. **flow-{{name}}.md** - One per key end-to-end flow
   - Frontmatter: title, repo, tags, created, updated
   - Sections: Trigger, Steps, Services Involved, Data Transformations, Error Cases

6. If cross-repo connections are identified, create articles under knowledge/repos/connections/

## Rules
- Use [[repos/{repo_name}/module-x]] style wikilinks between repo articles
- Link to existing [[concepts/...]] articles where relevant
- Every article must have complete YAML frontmatter
- Write in encyclopedia style - factual, concise, self-contained
- Include commit hash {commit} in overview frontmatter as last_scanned_commit
- Update knowledge/index.md with all new articles (add rows to the existing table)
- Append to knowledge/log.md with timestamp and list of created articles

## File paths for writing
- Repo articles: {repos_dir}/{repo_name}/
- Cross-repo connections: {repos_dir}/connections/
- Index file: {index_file}
- Log file: {log_file}

## Quality Standards
- Each module article should have at minimum 200 words of substantive content
- API articles should list ALL endpoints, not just a sample
- Data model articles should include actual field names and types
- Flow articles should trace the complete path through the codebase
- If you need to read source files for more detail, use the Read tool
"""


async def phase1_discover(repo_name: str, repo_path: Path) -> str:
    """Phase 1: Explore the repo and produce a JSON analysis manifest."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    prompt = PHASE1_PROMPT_TEMPLATE.format(
        repo_name=repo_name,
        repo_path=str(repo_path),
    )

    response = ""
    cost = 0.0

    print(f"  Phase 1: Discovering {repo_name} architecture...")
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(repo_path),
            system_prompt={"type": "preset", "preset": "claude_code"},
            allowed_tools=["Read", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=40,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response += block.text
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0

    print(f"  Phase 1 complete. Cost: ${cost:.2f}")
    return response


async def phase2_write_articles(
    repo_name: str,
    repo_path: Path,
    phase1_output: str,
    commit: str,
) -> float:
    """Phase 2: Create knowledge articles from the Phase 1 analysis."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    # Read schema
    schema = AGENTS_FILE.read_text(encoding="utf-8") if AGENTS_FILE.exists() else ""
    wiki_index = read_wiki_index()

    # Ensure output directories exist
    repo_articles_dir(repo_name)
    (REPOS_DIR / "connections").mkdir(parents=True, exist_ok=True)

    prompt = PHASE2_PROMPT_TEMPLATE.format(
        schema=schema,
        phase1_output=phase1_output,
        repo_path=str(repo_path),
        wiki_index=wiki_index,
        repo_name=repo_name,
        commit=commit,
        repos_dir=str(REPOS_DIR),
        index_file=str(INDEX_FILE),
        log_file=str(LOG_FILE),
    )

    response = ""
    cost = 0.0

    print(f"  Phase 2: Writing articles for {repo_name}...")
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(ROOT_DIR),
            system_prompt={"type": "preset", "preset": "claude_code"},
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=50,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response += block.text
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0

    print(f"  Phase 2 complete. Cost: ${cost:.2f}")
    return cost


async def scan_repo(repo_name: str, state: dict) -> float:
    """Full scan pipeline for a single repo. Returns total cost."""
    tracked = get_tracked_repos()
    remote_url = tracked[repo_name]

    # Step 1: Ensure clean clone exists and is up to date
    print(f"\n{'='*60}")
    print(f"Scanning: {repo_name}")
    print(f"{'='*60}")

    repo_path = ensure_clone(repo_name, remote_url)
    commit = get_current_commit(repo_path)
    print(f"  Commit: {commit[:12]}")

    # Step 2: Phase 1 discovery
    phase1_output = await phase1_discover(repo_name, repo_path)

    # Step 3: Phase 2 article writing
    cost = await phase2_write_articles(repo_name, repo_path, phase1_output, commit)

    # Step 4: Count created articles
    articles_dir = REPOS_DIR / repo_name
    article_count = len(list(articles_dir.rglob("*.md"))) if articles_dir.exists() else 0

    # Step 5: Update state (atomic to avoid race with parallel scans)
    repo_entry = {
        "last_scanned_commit": commit,
        "last_synced_commit": commit,
        "scanned_at": now_iso(),
        "synced_at": now_iso(),
        "scan_cost_usd": cost,
        "articles_created": article_count,
    }

    def _update(s):
        s.setdefault("repos", {})[repo_name] = repo_entry
        s["total_cost"] = s.get("total_cost", 0.0) + cost

    state = update_state(_update)

    print(f"\n  Scan complete: {article_count} articles, ${cost:.2f}")
    return cost


def main():
    parser = argparse.ArgumentParser(description="Deep-scan repos into knowledge base")
    parser.add_argument("--repo", type=str, help="Scan a specific repo by name")
    parser.add_argument("--force", action="store_true", help="Rescan even if already scanned")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be scanned")
    args = parser.parse_args()

    state = load_state()
    repos_state = state.get("repos", {})
    tracked = get_tracked_repos()

    if not tracked:
        print("No repos configured. Add one first:")
        print("  uv run python scripts/add_repo.py https://github.com/user/repo.git")
        sys.exit(1)

    if args.repo:
        if args.repo not in tracked:
            print(f"Error: '{args.repo}' is not a tracked repo.")
            print(f"Available: {', '.join(tracked.keys())}")
            sys.exit(1)
        repos_to_process = {args.repo: tracked[args.repo]}
    else:
        repos_to_process = dict(tracked)

    # Show plan
    print("\nRepo Knowledge Base Scanner")
    print("=" * 40)
    total_cost = 0.0

    for repo_name in repos_to_process:
        already_scanned = repo_name in repos_state
        if already_scanned and not args.force:
            scanned_at = repos_state[repo_name].get("scanned_at", "unknown")
            print(f"  {repo_name}: already scanned at {scanned_at} (use --force to rescan)")
            continue

        status = "RESCAN" if already_scanned else "NEW"
        print(f"  {repo_name}: [{status}] will be scanned")

        if args.dry_run:
            continue

        # Interactive confirmation
        if not args.repo:  # Only ask if scanning multiple
            answer = input(f"\n  Scan {repo_name}? [y/N] ").strip().lower()
            if answer != "y":
                print(f"  Skipping {repo_name}")
                continue

        cost = asyncio.run(scan_repo(repo_name, state))
        total_cost += cost

    if not args.dry_run and total_cost > 0:
        print(f"\nTotal scan cost: ${total_cost:.2f}")
        print(f"Cumulative total: ${state.get('total_cost', 0.0):.2f}")


if __name__ == "__main__":
    main()
