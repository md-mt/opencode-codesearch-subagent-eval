# OpenCode Subagent Eval: Codesearch vs Explore

Evaluation harness for comparing OpenCode's `codesearch` subagent (D100719588) against the existing `explore` subagent on EdenFS/fbsource.

## Prerequisites

- `opencode` CLI on PATH (Meta devserver with MetaCode installed)
- EdenFS-backed fbsource checkout (default: `/data/users/$USER/fbsource`)
- `jq` for the runner script
- Python 3.10+ for collection/comparison scripts

## Quick Start

```bash
# 1. Run eval cases (both agents, all 24 cases — takes ~2-3 hours)
./scripts/run_eval.sh

# Or run a subset
./scripts/run_eval.sh --cases 1,2,3 --agent codesearch

# 2. Collect results from session exports
python3 scripts/collect_results.py --batch-from-run results/run_YYYYMMDD_HHMMSS/

# 3. Compare results
python3 scripts/compare_results.py results/run_YYYYMMDD_HHMMSS/collected.json
```

## Repo Structure

```
cases/manifest.json          # 24 eval cases across 6 categories
scripts/
  run_eval.sh                # Runs opencode for each case × agent
  collect_results.py         # Extracts metrics from session exports
  compare_results.py         # Side-by-side comparison table
results/
  dryrun/                    # Sample results from Case #1
docs/
  design.md                  # Eval methodology and learnings
```

## Eval Categories

| Category | Cases | What it tests |
|----------|-------|---------------|
| Pinpoint lookup | #1-4 | Finding a specific class/function definition |
| Cross-cutting search | #5-8 | Finding patterns across many files |
| Architecture understanding | #9-12 | Synthesizing how a system works |
| API discovery | #13-16 | Finding how to use an internal API |
| Needle-in-haystack | #17-20 | Finding specific strings in a massive codebase |
| Broad exploration | #21-24 | Open-ended research across large subtrees |

## References

- Diff: D100719588
- Task: T263476439
- Design doc: `~/gdrive/01_projects/opencode-contribution/design/T263476439-code-search-subagent.md`
