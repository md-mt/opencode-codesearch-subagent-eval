# CLAUDE.md

Evaluation harness for comparing OpenCode's `codesearch` vs `explore` subagents.

## Key Facts

- This repo does NOT use Buck. Scripts are plain Python 3 and Bash.
- `opencode` CLI must be on PATH to run evals.
- The eval runs against fbsource on EdenFS (default: `/data/users/$USER/fbsource`).
- Results are stored in `results/<run_id>/` directories.

## Common Commands

```bash
# Run a single eval case
./scripts/run_eval.sh --cases 1 --agent codesearch

# Collect results after a run
python3 scripts/collect_results.py --batch-from-run results/<run_id>/

# Compare collected results
python3 scripts/compare_results.py results/<run_id>/collected.json

# Collect a single session
python3 scripts/collect_results.py --session <sessionID> --query "..." --agent codesearch
```

## Data Flow

1. `run_eval.sh` → invokes `opencode run` per case × agent → JSONL + sessions.json
2. `collect_results.py` → reads JSONL + `opencode export` + SQLite DB → collected.json
3. `compare_results.py` → reads collected.json → comparison table + summary stats
