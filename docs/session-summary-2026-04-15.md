# Session Summary: Codesearch vs Explore Eval — 2026-04-15

**Task:** T263476439 | **Diff:** D100719588 | **OpenCode:** 1.3.13+Meta

---

## 1. Finding: Explore Has MCP Access (Same Tools as Codesearch)

The `explore` subagent can use `meta_core_search_files` (indexed search) — the same backend `codesearch` uses. This was unexpected and changes what the eval measures.

**Root cause: `--yolo` flag overrides agent permissions.**

| Step | What happens |
|------|-------------|
| `run_eval.sh` passes `--yolo` | Needed for non-interactive mode (auto-approve permission prompts) |
| `--yolo` sets `OPENCODE_PERMISSION` | `{ "*": "allow", external_directory: { "*": "allow" } }` (`config.ts:1462`) |
| `agent.ts:162-179` | `Permission.merge(defaults, exploreConfig, user)` — yolo rules appended LAST |
| `permission/index.ts:302` | `Permission.disabled` uses `findLast` — yolo's `"*": "allow"` wins over explore's `"*": "deny"` |

**Without `--yolo`, explore's `"*": "deny"` correctly blocks MCP tools.** But `--yolo` is required for non-interactive eval. There is no `--yolo-but-respect-agent-permissions` option. Attempts to disable MCP via user config (`"mcp": {}`, `"disabled": true`, `command: ["/bin/false"]`) also failed.

**Implication:** The eval measures **prompt/strategy quality** (19-line generic vs 90-line 7-step methodology), not tool access. Both agents have identical tools. Still valuable — better prompts should yield better search strategy and synthesis.

---

## 2. Evaluation Approach and Results

### Infrastructure Built

| Component | Purpose |
|-----------|---------|
| `cases/manifest.json` | 24 cases across 6 categories |
| `scripts/run_eval.sh` | Runs `opencode run` per case × agent, writes JSONL |
| `scripts/collect_results.py` | Extracts metrics from session exports (3-layer: JSONL → parent export → subagent export via SQLite) |
| `scripts/compare_results.py` | Side-by-side comparison table + aggregate stats |

### Eval Categories (24 cases)

| Category | Cases | Tests |
|----------|-------|-------|
| Pinpoint lookup | #1-4 | Find specific class/function definition |
| Cross-cutting search | #5-8 | Patterns across many files |
| Architecture understanding | #9-12 | System-level synthesis |
| API discovery | #13-16 | How to use an internal API |
| Needle-in-haystack | #17-20 | Specific strings in massive codebase |
| Broad exploration | #21-24 | Open-ended research |

### Dry Run Results (Case #1: TaoClient)

| Metric | Codesearch | Explore | Ratio |
|--------|-----------|---------|-------|
| Subagent wall time | 56.0s | 262.9s | 4.7x |
| Subagent input tokens | 5,195 | 51,006 | 9.8x |
| Subagent tool calls | 4 | 6 | 1.5x |
| Parent response | 3,612 chars | 2,445 chars | — |

### Current Status

- **Explore baseline:** 24 cases running (sequential, ~5 min each)
- **Codesearch baseline:** Pending (will run after explore completes)
- **Comparison:** Will merge both `collected.json` files and run `compare_results.py`

### Key Technical Learnings

- `--pure` flag breaks LLM auth — cannot isolate agents this way
- JSONL output buffered until process exit — real data in session exports
- `opencode export` output can exceed 64KB — must use temp file (pipe truncation)
- Export parts: `type: "tool"` (not "tool-use"), `step-finish` has `cost` and `tokens`

---

## 3. Prior Art: Code Search Team's mma_search Dataset

Aahan Aggarwal (2026-03-25) evaluated `meta-rg` CLI vs MCP `search_files` — testing tool interface, not prompt quality.

### Their Results

| Metric | MCP 5ctx | MCP 0ctx | CLI |
|--------|----------|----------|-----|
| Pass rate | 65% | 68% | 64% |
| Avg cost | $0.53 | $0.56 | $0.78 |
| Avg time | 157s | 165s | 187s |
| Avg tool calls | 18.2 | 19.3 | 25.2 |

### Methodology Comparison

| Dimension | Aahan's Eval | Our Eval |
|-----------|-------------|----------|
| Framework | MSL Judge (`fbcode/msl/judge/`) on Sandcastle | Custom scripts, local sequential |
| Test cases | 100 from mma_search (ground truth file paths) | 24 hand-crafted, 6 categories |
| Judging | LLM-as-Judge + weighted quality factors | Automated metrics only |
| Quality scoring | search_strategy / tool_effectiveness / path_accuracy | None (planned) |

### Their Failure Modes (applicable to us)

1. **Terminology mismatch** — query uses different terms than code (biggest)
2. **Wrong directory scope** — agent assumes wrong part of monorepo
3. **Settling too early** — finds plausible file without checking alternatives
4. **Query spiral** — 30-100+ queries trying minor variations

### Key Resources

- **mma_search data:** `manifold://devai/tree/synthetic-benchmarks/code_retrieval_synthetic_benchmark_v2_1000.csv` (1000 cases)
- **MSL Judge:** `fbcode/msl/judge/clients/claude_code.py`
- **Eval config:** `fbsource/tools/devmate/evals/devai/mma_search_v2.yaml`

### Planned Enhancement

Import mma_search cases + add LLM-as-judge scoring via `scripts/import_mma_search.py` and `scripts/judge_results.py`.
