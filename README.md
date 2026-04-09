# Repo Brain - AI Knowledge Base Compiler

**Your AI conversations + your codebases compile themselves into a searchable knowledge base.**

Adapted from [Karpathy's LLM Knowledge Base](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and [Cole Medin's Claude Memory Compiler](https://github.com/coleam00/claude-memory-compiler). This fork adds **deep repository understanding** - point it at any git repo and it builds structured, queryable knowledge articles covering architecture, APIs, data models, and key flows.

## What It Does

1. **Conversation capture** - Claude Code hooks automatically extract knowledge from your AI coding sessions
2. **Repo deep scan** - One-time LLM-powered analysis of any git repo (architecture, modules, APIs, models, flows)
3. **Daily sync** - Auto-pulls repos and incrementally updates articles from git diffs
4. **Dev activity tracking** - Captures your commits and uncommitted changes from working copies
5. **Index-guided retrieval** - Ask questions and get answers with wikilink citations (no RAG, no vector DB)

## Setup via Claude Code (Recommended)

The easiest way to set up Repo Brain is to let Claude do it. Open Claude Code in any directory and paste this prompt:

> Clone https://github.com/mkmallik/repo-brain.git into a directory of your choice. Then:
> 1. Run `uv sync` to install dependencies
> 2. Add my repos to the knowledge base using `uv run python scripts/add_repo.py <remote_url>`. For each repo I work on locally, also pass `--working-copy /path/to/my/local/copy` so dev activity gets captured.
> 3. Run `uv run python scripts/scan_repo.py` to deep-scan all added repos into the knowledge base (this uses a two-phase LLM approach and may take a few minutes per repo).
> 4. Set up global Claude Code hooks in `~/.claude/settings.json` so every future session has access to the knowledge base. The hooks need absolute paths with `--directory` pointing to where you cloned repo-brain. Add SessionStart (runs `hooks/session-start.py`, timeout 15), SessionEnd (runs `hooks/session-end.py`, timeout 10), and PreCompact (runs `hooks/pre-compact.py`, timeout 10).
> 5. Read AGENTS.md for the full technical reference.

Claude will handle cloning, installing, scanning your repos, and configuring the hooks. Once done, every new Claude Code session — in any project — will have your knowledge base injected automatically.

## Manual Setup

```bash
# 1. Clone and install
git clone https://github.com/mkmallik/repo-brain.git
cd repo-brain
uv sync

# 2. Add your repos (examples using CopperOne Healthcare Platform)
uv run python scripts/add_repo.py https://github.com/CopperOneOrg/coplatform.git
uv run python scripts/add_repo.py https://github.com/CopperOneOrg/doc-process-27x7.git
uv run python scripts/add_repo.py https://github.com/CopperOneOrg/docread-consumer.git
uv run python scripts/add_repo.py https://github.com/CopperOneOrg/oasis-llm-processor.git
uv run python scripts/add_repo.py https://github.com/CopperOneOrg/patient-management-fe.git

# Optionally link your local working copies (for dev activity capture)
uv run python scripts/add_repo.py https://github.com/CopperOneOrg/coplatform.git \
  --name coplatform \
  --working-copy ~/projects/coplatform

# 3. Scan repos into the knowledge base
uv run python scripts/scan_repo.py --repo coplatform
uv run python scripts/scan_repo.py  # interactive, scans all

# 4. Set up global hooks (works from any Claude Code session)
# Add to ~/.claude/settings.json:
```

```json
{
  "hooks": {
    "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "uv run --directory /path/to/repo-brain python hooks/session-start.py", "timeout": 15}]}],
    "PreCompact": [{"matcher": "", "hooks": [{"type": "command", "command": "uv run --directory /path/to/repo-brain python hooks/pre-compact.py", "timeout": 10}]}],
    "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": "uv run --directory /path/to/repo-brain python hooks/session-end.py", "timeout": 10}]}]
  }
}
```

## How It Works

```
                          ┌─────────────────────────────────────────────┐
                          │            YOUR KNOWLEDGE BASE              │
                          │  knowledge/                                 │
                          │  ├── index.md          (master catalog)     │
                          │  ├── concepts/         (from conversations) │
                          │  ├── connections/      (cross-cutting)      │
                          │  ├── qa/               (saved answers)      │
                          │  └── repos/            (from code repos)    │
                          │      ├── backend-api/  (overview, modules,  │
                          │      │                  models, apis, flows)│
                          │      ├── frontend/                         │
                          │      └── connections/  (cross-repo links)   │
                          └─────────────────────────────────────────────┘
                                    ▲                       ▲
                                    │                       │
                    ┌───────────────┘                       └───────────────┐
                    │                                                       │
          ┌─────────────────┐                                 ┌─────────────────┐
          │  CONVERSATIONS  │                                 │   CODE REPOS    │
          │                 │                                 │                 │
          │ session-end.py  │                                 │ scan_repo.py    │
          │ → flush.py      │                                 │ (one-time deep) │
          │ → daily log     │                                 │                 │
          │ → compile.py    │                                 │ sync_repos.py   │
          │ → articles      │                                 │ (daily pull +   │
          └─────────────────┘                                 │  incremental)   │
                                                              └─────────────────┘
```

## Key Commands

### Repo Management
```bash
uv run python scripts/add_repo.py <remote_url>                    # add a repo
uv run python scripts/add_repo.py <url> --working-copy ~/dev/app  # add with local copy
uv run python scripts/add_repo.py --list                          # list tracked repos
uv run python scripts/add_repo.py --remove old-repo               # remove a repo
```

### Scanning & Syncing
```bash
uv run python scripts/scan_repo.py                     # scan all unscanned repos (interactive)
uv run python scripts/scan_repo.py --repo backend-api  # scan a specific repo
uv run python scripts/scan_repo.py --repo backend-api --force  # rescan
uv run python scripts/sync_repos.py                    # pull all repos + update articles
uv run python scripts/sync_repos.py --dry-run          # preview what would change
```

### Knowledge Base
```bash
uv run python scripts/compile.py                       # compile daily conversation logs
uv run python scripts/query.py "How does auth work?"   # ask the knowledge base
uv run python scripts/query.py "question" --file-back  # ask + save answer as article
uv run python scripts/lint.py                          # run health checks
uv run python scripts/lint.py --structural-only        # free structural checks only
```

## Repo Scanning: Two-Phase Approach

When you scan a repo, the system uses a two-phase LLM approach:

1. **Phase 1 (Discovery)** - LLM explores the repo using Read/Glob/Grep tools, produces a structured JSON manifest of modules, models, APIs, and flows
2. **Phase 2 (Writing)** - LLM creates structured markdown articles from the manifest

This produces 15-40 articles per repo covering:
- **Overview** - Architecture, tech stack, directory structure
- **Modules** - Purpose, public API, internal architecture, key functions
- **Data Models** - Schema, fields, relationships, validation
- **API Endpoints** - Routes, auth, request/response, error handling
- **Key Flows** - End-to-end workflows with steps and services involved
- **Cross-Repo Connections** - How repos integrate with each other

## Automatic Updates

Once hooks are configured:

- **Session start** - KB index + repo summary injected into every Claude session
- **Session end** - Conversation highlights flushed to daily log
- **End of day (6 PM)** - Auto-compiles daily log + syncs repos + captures dev activity from working copies
- **Pre-compact** - Safety capture before long sessions auto-compact

## Obsidian Integration

The knowledge base is pure markdown with `[[wikilinks]]`. Point Obsidian at the `knowledge/` directory for graph view, backlinks, and search.

## Why No RAG?

At personal scale (50-500 articles), the LLM reading a structured `index.md` outperforms vector similarity. The LLM understands what you're really asking; cosine similarity just finds similar words. RAG becomes necessary at ~2,000+ articles.

## Configuration

All repo configuration lives in `repos.json`:

```json
{
  "repos": {
    "coplatform": {
      "remote": "https://github.com/CopperOneOrg/coplatform.git",
      "working_copy": "/Users/you/projects/coplatform"
    },
    "doc-process-27x7": {
      "remote": "https://github.com/CopperOneOrg/doc-process-27x7.git",
      "working_copy": "/Users/you/projects/doc-process-27x7"
    },
    "patient-management-fe": {
      "remote": "https://github.com/CopperOneOrg/patient-management-fe.git"
    }
  }
}
```

- `remote` (required) - Git URL for cloning into `secondbrain-repo/`
- `working_copy` (optional) - Path to your local dev copy for activity capture

## Dependencies

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) (uses your existing Claude subscription)

## Technical Reference

See **[AGENTS.md](AGENTS.md)** for the complete technical reference: article formats, hook architecture, script internals, and customization options.

## Credits

- Original concept: [Andrej Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- Base implementation: [Cole Medin](https://github.com/coleam00/claude-memory-compiler)
- Repo knowledge system: Enhanced fork
