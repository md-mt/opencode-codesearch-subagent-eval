# Evaluation Design: Codesearch vs Explore Subagent

## Background

D100719588 adds a `codesearch` subagent to OpenCode, optimized for deep codebase exploration on EdenFS. This eval compares it against the existing `explore` subagent across 24 use cases targeting fbsource at scale.

## How OpenCode Runs Non-Interactively

```bash
opencode run "your query here" --format json --yolo
```

- `--format json` emits NDJSON events (step_start, tool_use, text, step_finish) to stdout
- `--yolo` auto-approves all permissions
- `--agent <name>` selects primary agent only; subagents are selected via prompt instruction ("Use @codesearch to...")
- `--pure` strips Meta plugins but also breaks LLM auth — do NOT use

## Session Storage

Sessions are persisted in SQLite at `~/.local/share/opencode/opencode.db`.

Schema:
- `session` table: id, parent_id, title, time_created, time_updated
- `message` table: id, session_id, data (JSON with role)
- `part` table: id, message_id, session_id, data (JSON with type, tool, state, text, tokens)

Subagent sessions have `parent_id` set to the parent session ID.

## Session Export Format

`opencode export <sessionID>` produces JSON with:
- `info`: session metadata (id, title, time.created, time.updated, turnCount)
- `messages[]`: each with `info.role` and `parts[]`

Part types:
- `"tool"`: tool call with `.tool`, `.state.status`, `.state.input`, `.state.output`, `.state.time`
- `"text"`: assistant text with `.text`
- `"step-finish"`: completion with `.cost`, `.tokens` (input/output/reasoning/cache.read/cache.write)
- `"step-start"`, `"reasoning"`, `"agent"`: metadata

**Important**: Export output can exceed 64KB. Must write to a temp file instead of capturing via subprocess pipe (pipe buffer truncation).

## Dry Run Results (Case #1: TaoClient)

Query: "Find the definition of the TaoClient class in fbsource"

| Metric | Codesearch | Explore |
|--------|-----------|---------|
| Parent wall time | 308.6s | 327.2s |
| Subagent wall time | 56.0s | 262.9s |
| Subagent tool calls | 4 (1 glob, 3 grep) | 6 (1 search_files, 5 read) |
| Subagent input tokens | 5,195 | 51,006 |
| Parent response | 3,612 chars | 2,445 chars |
| Subagent response | 0 chars | 7,635 chars |

Key observations:
- Both agents used `meta_core_search_files` at the parent level
- The `explore` subagent was **4.7x slower** and used **10x more input tokens**
- The redirect hook did NOT fire — both ran their intended subagent
- JSONL output is buffered until process exit; real data is in session exports

## Building Custom MetaCode Builds

Reference: [MetaCode — Testing Custom Builds](https://docs.google.com/document/d/1yqyQ_lgeyxHGTgvvVJBWNFrOI149SdTZx0UwU0B7vfQ/edit)

```bash
# Build the binary
buck2 build fbcode//3pai_tooling/metacode:metacode-binary

# Test locally with custom build
METACODE_BINARY_OVERRIDE=$(buck2 build fbcode//3pai_tooling/metacode:metacode-binary --show-full-simple-output) metacode

# Dev iteration with full source tree
export METACODE_DEV_REPO=~/fbsource
```
