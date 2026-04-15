# Evaluation Design: Codesearch vs Explore Subagent

**Diff:** D100719588
**Task:** T263476439
**Date:** 2026-04-14

---

## 1. Problem Statement

OpenCode's existing `explore` subagent is a generic "file search specialist" with a minimal 19-line system prompt. It uses basic `grep`, `glob`, `bash`, and `read` tools — standard recursive filesystem operations that work for small repos but **time out on fbsource (10M+ files on EdenFS)**.

Claude Code @ Meta solves this with a dedicated `meta_codesearch:code_search` subagent that uses **indexed search tools** (like `search_files` MCP) instead of filesystem traversals, and has a rich system prompt guiding search strategies.

D100719588 brings a similar `codesearch` subagent to OpenCode via the Meta layer (`managed-settings.json` + `packages/meta/server.ts`), without touching core opencode.

### Key Differences

| Aspect | OpenCode `explore` | New `codesearch` |
|--------|-------------------|------------------|
| **Focus** | Generic file exploration | Code search specialist |
| **Tools** | grep, glob, bash, read | grep, glob, bash, read, MCP (search_files) |
| **Search backend** | Recursive filesystem (rg) | Indexed search (search_files MCP) |
| **System prompt** | 19 lines, generic | 90 lines, 7-step methodology |
| **Result handling** | Returns raw findings | Summarizes and synthesizes |

### Codesearch 7-Step Methodology

The `codesearch` agent's system prompt (borrowed from CC@Meta's `code_search` plugin) follows:

1. **Goal Clarification** — understand what the user is seeking
2. **Documentation search** — use knowledge/doc tools when available
3. **Vague query handling** — use semantic/indexed search for hints first
4. **Strategic Search Planning** — plan searches from broad to specific
5. **Efficient Search Execution** — prefer indexed search tools (MCP), fall back to grep/glob
6. **Selective Analysis** — read judiciously, not entire files
7. **Concise Synthesis** — structured output (Direct Answer → Key Locations → Code Summary → Context → Next Steps)

### Architecture

```
Main Agent (build)
  |
  |-- task tool call (subagent_type: "explore")     ← LLM's initial choice
  |
  v
Meta Hook (tool.execute.before)
  |-- EdenFS? → DENY: "Use codesearch instead"      ← redirect
  |-- Non-EdenFS? → pass through                    ← no-op
  |
  v
Main Agent retries with (subagent_type: "codesearch")
  |
  v
Code Search Subagent (child session)
  |-- uses search_files MCP (indexed, fast)
  |-- uses grep/glob (scoped, as fallback)
  |-- uses read (targeted file reading)
  |-- synthesizes findings with 7-step methodology
  |
  |-- returns summary in <task_result>
  v
Main Agent receives concise summary
  (raw search results stay in child session)
```

### Subagent Communication Protocol

1. **Dispatch:** Main agent calls `task` tool with `{ prompt, subagent_type }` → child session created
2. **Execution:** Subagent runs autonomously with its own tools/permissions
3. **Return:** Only the **last text part** of the subagent's response is returned in `<task_result>` tags (see `task.ts:146-154`)
4. **Resumption:** `task_id` allows continuing the same child session

---

## 2. Evaluation Methodology

### 2.1 Approach

We run the same set of code search queries against both `explore` and `codesearch` subagents on an EdenFS-backed fbsource checkout, then compare:

- **Wall time** — how long the subagent takes
- **Token usage** — input tokens consumed (proxy for cost)
- **Tool calls** — number and types of tools used
- **Response quality** — length and content of final response (manual review)

### 2.2 Methodology Options Considered

**A. LLM-as-Judge with Ground Truth Anchors:** Curate queries with pre-researched expected answers. A grader LLM scores responses on correctness/completeness. Reproducible but labor-intensive to curate ground truth.

**B. Head-to-Head Human Ranking:** Present both outputs anonymized, human judges pick a winner. Captures subjective quality but doesn't scale.

**C. Automated Metrics Only:** Measure success rate, latency, tokens, tool calls. Fully automated but doesn't measure answer quality.

**D. Hybrid (Automated + LLM-as-Judge):** Combine C and A. Collect automated metrics AND run an LLM grader. Best of both worlds.

For this initial eval, we use **approach C (automated metrics)** as a first pass, with manual review of responses for quality signals. If the results are ambiguous, we can layer on LLM-as-judge grading later.

### 2.3 Existing Eval Infrastructure

The **`claude_eval` framework** at `fbcode/devai/claude_eval/` was considered but is not directly usable:

- YAML-driven eval harness that runs `claude` CLI (not `opencode`) against test cases
- Has `StaticAdapterConfig` for inline test cases and Jinja2 prompt templates
- **Variant system is a stub** — declared in dataclass but not wired into job creation
- Grading is inline self-eval only (`{did_pass, score, reasoning}`)
- No mechanism to force which subagent Claude uses (only prompt instructions)
- Results flow to `claude_code_eval_events` Scuba table

**Decision:** Build a lightweight custom harness (`run_eval.sh` + `collect_results.py` + `compare_results.py`) rather than adapting `claude_eval`.

---

## 3. Running Evaluations

### 3.1 Non-Interactive Execution

OpenCode's `run` subcommand supports non-interactive execution:

```bash
opencode run "your query here" --format json --yolo
```

- `--format json` — emits NDJSON events (step_start, tool_use, text, step_finish) to stdout
- `--yolo` — auto-approves all permissions
- `--agent <name>` — selects primary agent only; **cannot select subagents directly**. Must instruct via prompt ("Use @codesearch to...")
- `--dir <path>` — set working directory
- `--pure` — strips Meta plugins but also breaks LLM auth — **do NOT use**

### 3.2 Capturing Subagent Output

**What the main agent sees:** Only the last text part from the subagent, wrapped in `<task_result>` tags (`task.ts:146-163`).

**Full subagent conversation:** Persisted in SQLite at `~/.local/share/opencode/opencode.db`. Subagent sessions have `parent_id` linking to parent.

```bash
# Export parent session
opencode export <sessionID>

# Find child (subagent) sessions
sqlite3 ~/.local/share/opencode/opencode.db \
  "SELECT id FROM session WHERE parent_id = '<sessionID>'"

# Export each child session
opencode export <childSessionID>
```

### 3.3 Session Storage

Sessions are persisted in SQLite at `~/.local/share/opencode/opencode.db`.

Schema:
- `session` table: id, parent_id, title, time_created, time_updated
- `message` table: id, session_id, data (JSON with role)
- `part` table: id, message_id, session_id, data (JSON with type, tool, state, text, tokens)

### 3.4 Session Export Format

`opencode export <sessionID>` produces JSON with:
- `info`: session metadata (id, title, time.created, time.updated, turnCount)
- `messages[]`: each with `info.role` and `parts[]`

Part types:
- `"tool"`: tool call with `.tool`, `.state.status`, `.state.input`, `.state.output`, `.state.time`
- `"text"`: assistant text with `.text`
- `"step-finish"`: completion with `.cost`, `.tokens` (input/output/reasoning/cache.read/cache.write)
- `"step-start"`, `"reasoning"`, `"agent"`: metadata

**Important**: Export output can exceed 64KB. Must write to a temp file instead of capturing via subprocess pipe (pipe buffer truncation at 64KB boundary).

### 3.5 Key Source Files

| File | Purpose |
|------|---------|
| `packages/opencode/src/cli/cmd/run.ts` | CLI `run` command (non-interactive mode) |
| `packages/opencode/src/tool/task.ts` | Task/subagent tool dispatch + result extraction |
| `packages/opencode/src/cli/cmd/export.ts` | Session export command |
| `packages/opencode/src/session/session.sql.ts` | Session DB schema |
| `packages/opencode/src/storage/db.ts` | DB path (`~/.local/share/opencode/opencode.db`) |
| `fbcode/3pai_tooling/tpai/tpai_opencode/src/trajectory.rs` | Reference trajectory reader for DB queries |

---

## 4. Eval Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  run_eval.sh                                                    │
│  For each case × agent:                                         │
│    opencode run "<prompt>" --format json --yolo                 │
│    → results/<run_id>/{agent}_q{N}.jsonl                       │
│    → results/<run_id>/sessions.json                            │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        v
┌─────────────────────────────────────────────────────────────────┐
│  collect_results.py --batch-from-run results/<run_id>/         │
│  For each case × agent:                                         │
│    Parse JSONL → extract session ID                             │
│    opencode export <sessionID> → parent metrics                 │
│    SQLite query → find child sessions                           │
│    opencode export <childID> → subagent metrics                 │
│    → results/<run_id>/collected.json                           │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        v
┌─────────────────────────────────────────────────────────────────┐
│  compare_results.py results/<run_id>/collected.json            │
│  Per-case comparison table + aggregate stats                    │
│  → stdout (table) + optional summary.json                      │
└─────────────────────────────────────────────────────────────────┘
```

**Constraint:** The EdenFS redirect hook denies `explore` and tells the model to use `codesearch`. In the dry run, the redirect hook did NOT fire — both agents ran independently. If the hook does fire during eval, it means `explore` cases will actually test `codesearch` via redirect (which is still useful data — it measures the redirect overhead).

### Important Finding: MCP Tools Available to Both Agents

**Both `explore` and `codesearch` have access to `meta_core_search_files` (MCP indexed search).** This was discovered during eval setup:

- MCP tools are injected globally in `prompt.ts:500-582` — `mcp.tools()` returns all tools from all connected MCP servers with no agent-level filtering.
- The explore agent's `"*": "deny"` permission should block MCP tools, but `Permission.merge(defaults, agentConfig, user)` applies user config last, and `findLast` means later rules win.
- The system-level MCP config at `/etc/opencode/opencode.json` (read-only, root-owned) injects `meta_core` and cannot be overridden from user config.
- Attempts to disable MCP via user config (`"mcp": {}`, `"disabled": true`, `command: ["/bin/false"]`) all failed — the system config takes precedence.

**Implication:** This eval measures **prompt/strategy quality** (19-line generic prompt vs 90-line 7-step methodology), NOT tool access differences. Both agents have the same search tools available. This is still a valid comparison — a better prompt should produce better search strategy, synthesis, and response quality even with identical tool access.

---

## 5. Evaluation Cases

24 cases across 6 categories, all targeting fbsource at scale on EdenFS.

### Category 1: Pinpoint Class/Function Lookup (4 cases)
> Clear right answer — did the agent find the exact definition?

| # | Query | Why it's hard on EdenFS |
|---|-------|------------------------|
| 1 | "Find the definition of the `TaoClient` class" | Core infra class, deeply nested in fbcode |
| 2 | "Where is `getGatekeeperValue` implemented?" | GK is used everywhere; need the definition, not call sites |
| 3 | "Find the Thrift definition for the `UserProfile` struct" | .thrift files scattered across fbcode |
| 4 | "Where is the `BucketizedCounter` class defined in folly?" | Need to search within third-party/folly |

### Category 2: Cross-Cutting Pattern Search (4 cases)
> Multiple files match; need completeness and relevance filtering.

| # | Query | Why it's hard on EdenFS |
|---|-------|------------------------|
| 5 | "Find all Thrift services that have a method named `getUser`" | Thousands of .thrift files across fbcode |
| 6 | "Which Python files use the `@retry` decorator in fbcode?" | Massive Python codebase, many false positives |
| 7 | "Find all BUCK targets that use `python_unittest` in fbcode/common/" | Hundreds of BUCK files |
| 8 | "Find all implementations of the `CacheInterface` across fbcode" | Abstract interface with many concrete implementations |

### Category 3: Architecture Understanding (4 cases)
> Requires reading multiple files and synthesizing how a system works.

| # | Query | Why it's hard on EdenFS |
|---|-------|------------------------|
| 9 | "How does Configerator config loading work end-to-end?" | Spans multiple layers (client, server, codegen) |
| 10 | "Explain how Tupperware job scheduling works" | Large infra system, many components |
| 11 | "How does the Buck2 remote execution system dispatch build actions?" | Deep build system internals |
| 12 | "How does the Scuba write path work?" | Cross-cutting data infra |

### Category 4: API/Usage Discovery (4 cases)
> Engineer needs to learn how to use an internal API.

| # | Query | Why it's hard on EdenFS |
|---|-------|------------------------|
| 13 | "How do I create a new Scuba table?" | Need docs + code examples |
| 14 | "Show me examples of how to use the ServiceRouter client in C++" | API discovery in large C++ codebase |
| 15 | "What's the API for sending a Workplace post programmatically?" | Need to find the right library among many |
| 16 | "How do I add a new GK (Gatekeeper) check in Python?" | Need pattern + registration code |

### Category 5: Needle-in-Haystack (4 cases)
> Specific string/error code buried deep in the repo.

| # | Query | Why it's hard on EdenFS |
|---|-------|------------------------|
| 17 | "Find the file that defines error code `E1234567`" | Specific constant in a massive codebase |
| 18 | "Which file handles the `X-FB-Request-ID` HTTP header?" | Specific header string across web infra |
| 19 | "Find where `slow_query_threshold_ms` config key is read and used" | Config key in a haystack |
| 20 | "Find the source of the log message 'Failed to connect to upstream service'" | Exact string search at scale |

### Category 6: Broad Exploration (4 cases)
> Open-ended research across large subtrees.

| # | Query | Why it's hard on EdenFS |
|---|-------|------------------------|
| 21 | "What logging frameworks are available in fbcode?" | Need to discover and compare multiple libraries |
| 22 | "Map out the authentication/authorization middleware stack" | Spans multiple systems |
| 23 | "What are the different caching layers available in fbcode infra?" | Discovery across many directories |
| 24 | "How is feature flagging implemented across mobile vs backend?" | Cross-platform comparison |

---

## 6. Results Collection

### collect_results.py

Extracts metrics from OpenCode sessions. Three input modes:

```bash
# Single session
python3 scripts/collect_results.py --session <sessionID> --query "..." --agent codesearch

# Batch from manifest (custom format)
python3 scripts/collect_results.py --batch manifest.json -o results.json

# From a run_eval.sh output directory (auto-discovers JSONL files + sessions)
python3 scripts/collect_results.py --batch-from-run results/<run_id>/
```

Output JSON per session includes:
- `parent_metrics`: wall_time_ms, tokens (input/output/reasoning/cache), tool_calls_summary, tool_calls_detail, final_response, turn_count, cost
- `subagent_metrics[]`: same fields for each child (subagent) session
- `jsonl_metrics`: events from the NDJSON stream (less detailed)

### Data Extraction Spec

Each eval run produces a result JSON with three layers of data:

**Layer 1: JSONL stream** (`opencode run --format json` stdout)
- Events are buffered until process exit — not useful for real-time monitoring
- Provides `sessionID` (used to look up the full session in the DB)
- Event types: `step_start`, `tool_use`, `text`, `step_finish`, `error`
- Limitation: tool names and text content are often empty in JSONL events

**Layer 2: Parent session export** (`opencode export <parentSessionID>`)
- The main agent's full conversation including its tool calls
- Contains the `task` tool call (which spawned the subagent) with duration
- Contains the final synthesized response the user would see
- Token usage per step (input/output/reasoning/cache.read/cache.write)

**Layer 3: Subagent session export** (SQLite `parent_id` lookup → `opencode export <childSessionID>`)
- The subagent's internal conversation — all search tool calls, intermediate reads
- Shows exactly which tools the subagent used (grep, glob, read, search_files, etc.)
- Contains the subagent's raw response (before parent synthesizes it)
- Independent timing and token counts

**Finding child sessions:**
```bash
sqlite3 ~/.local/share/opencode/opencode.db \
  "SELECT id, title FROM session WHERE parent_id = '<parentSessionID>'"
```

### Output JSON Schema

Each result JSON (per case × agent) has this structure:

```json
{
  "query": "Find the definition of the TaoClient class",
  "agent": "explore",
  "session_id": "ses_...",              // parent session ID
  "subagent_sessions": ["ses_..."],     // child session IDs
  "parent_metrics": {
    "session_id": "ses_...",
    "title": "...",                     // auto-generated session title
    "wall_time_ms": 308649,             // total parent session duration
    "message_count": 6,                 // number of messages in conversation
    "tool_calls_summary": [             // tool usage counts
      {"tool": "task", "count": 1},
      {"tool": "meta_core_search_files", "count": 1}
    ],
    "tool_calls_detail": [              // per-call details
      {
        "tool": "task",
        "status": "completed",
        "duration_ms": 68974,           // how long the subagent took
        "input_preview": "{'subagent_type': 'explore', ...}"
      }
    ],
    "total_tool_calls": 3,
    "final_response": "## TaoClient...", // the response the user sees
    "turn_count": 1,
    "tokens": {                          // aggregated across all steps
      "input": 39782,
      "output": 1519,
      "reasoning": 166,
      "cache_read": 63808,
      "cache_write": 0
    },
    "cost": 0
  },
  "subagent_metrics": [                  // one entry per child session
    {
      "session_id": "ses_...",
      "title": "Find TaoClient definition (@explore subagent)",
      "wall_time_ms": 262908,            // subagent-only duration
      "tool_calls_summary": [
        {"tool": "meta_core_search_files", "count": 1},
        {"tool": "read", "count": 5}
      ],
      "tool_calls_detail": [...],
      "total_tool_calls": 6,
      "final_response": "Based on my search...",  // subagent's raw response
      "tokens": {
        "input": 51006,
        "output": 2812,
        "reasoning": 338,
        "cache_read": 159808,
        "cache_write": 0
      }
    }
  ],
  "jsonl_metrics": { ... },              // parsed from JSONL (less detailed)
  "error": null
}
```

**Key metrics for comparison:**
- `subagent_metrics[0].wall_time_ms` — how long the search agent took (excludes parent overhead)
- `subagent_metrics[0].tokens.input` — input tokens consumed (cost proxy)
- `subagent_metrics[0].total_tool_calls` — efficiency of search strategy
- `parent_metrics.final_response` — what the user actually sees (quality)
- `subagent_metrics[0].tool_calls_summary` — which tools were used (grep vs search_files)

### compare_results.py

Reads collected results and produces:
- Per-case comparison table (wall time, tokens, winner)
- Aggregate stats (win rate, average time ratio, average token ratio)
- Optional JSON summary output

---

## 7. Dry Run Results (Case #1: TaoClient)

Query: "Find the definition of the TaoClient class in fbsource"

| Metric | Codesearch | Explore |
|--------|-----------|---------|
| Parent wall time | 308.6s | 327.2s |
| Subagent wall time | 56.0s | 262.9s |
| Subagent tool calls | 4 (1 glob, 3 grep) | 6 (1 search_files, 5 read) |
| Subagent input tokens | 5,195 | 51,006 |
| Parent response | 3,612 chars | 2,445 chars |
| Subagent response | 0 chars | 7,635 chars |

### Key Observations

1. Both agents used `meta_core_search_files` at the parent level, not just in the subagent
2. The `explore` subagent was **4.7x slower** and used **10x more input tokens**
3. The redirect hook did NOT fire — both ran their intended subagent
4. `--pure` flag causes opencode to hang (no LLM auth without Meta plugins) — cannot use it to bypass redirect hook
5. JSONL output is buffered until process exit; real data is in session exports
6. `opencode export` output can exceed 64KB — must use temp file to avoid pipe buffer truncation
7. Export JSON structure: parts have `type: "tool"` (not "tool-use"), `step-finish` has `cost` and `tokens`

---

## 8. Building Custom MetaCode Builds

Reference: [MetaCode — Testing Custom Builds](https://docs.google.com/document/d/1yqyQ_lgeyxHGTgvvVJBWNFrOI149SdTZx0UwU0B7vfQ/edit)

```bash
# Build the binary
buck2 build fbcode//3pai_tooling/metacode:metacode-binary

# Test locally with custom build
METACODE_BINARY_OVERRIDE=$(buck2 build fbcode//3pai_tooling/metacode:metacode-binary --show-full-simple-output) metacode

# Dev iteration with full source tree
export METACODE_DEV_REPO=~/fbsource
```

## 9. References

- Design doc: `~/gdrive/01_projects/opencode-contribution/design/T263476439-code-search-subagent.md`
- CC@Meta code_search plugin: `fbcode/3pai_tooling/claude_code/plugins/meta_codesearch/`
- CC@Meta code_search hook: `fbcode/3pai_tooling/claude_code/code_search_hook.py`
- OpenCode managed settings: `fbcode/3pai_tooling/opencode/managed-settings.json`
- Meta plugin: `packages/meta/src/server.ts`
- claude_eval framework: `fbcode/devai/claude_eval/`

---

## 10. Prior Art: Aahan's CLI vs MCP Eval

**Source:** [Workplace post](https://fb.workplace.com/groups/csi.eng/permalink/2387794448386283/) by Aahan Aggarwal (2026-03-25)

Aahan evaluated an `rg`-like CLI (`meta-rg`) against the MCP (`search_files`) for agent code search — a different variable than ours (tool interface vs subagent prompt quality), but with a significantly more rigorous evaluation methodology.

### Results

| Metric | MCP 5ctx | MCP 0ctx | CLI |
|--------|----------|----------|-----|
| Pass rate | 65% | 68% | 64% |
| Avg cost | $0.53 | $0.56 | $0.78 |
| Median cost | $0.40 | $0.39 | $0.59 |
| Avg time | 157s | 165s | 187s |
| Avg tool calls | 18.2 | 19.3 | 25.2 |

**TLDR:** MCP with 5-line context wins — it lets the agent glance at surrounding context to quickly discard irrelevant files. CLI naming and arg mismatch hurts (46% of tests require `--help` lookups).

### Methodology Differences vs Our Eval

| Dimension | Aahan's Eval | Our Eval |
|-----------|-------------|----------|
| **Framework** | MSL Judge (`fbcode/msl/judge/`) on Sandcastle | Custom scripts, local sequential runs |
| **Test cases** | 100 from mma_search benchmark (file-finding, ground truth file paths) | 24 hand-crafted across 6 categories (no ground truth) |
| **Judging** | LLM-as-Judge with weighted quality factors + critical pass/fail requirements | Automated metrics only (wall time, tokens, tool calls) |
| **Quality scoring** | search_strategy (0.34), tool_effectiveness (0.33), path_accuracy (0.33) | None |
| **Qualitative** | LLM-compiled failure mode taxonomy | Manual review |

### Failure Modes (applicable to our eval)

1. **Terminology mismatch (biggest):** Query says "partnership ads" but code uses `branded_content`
2. **Wrong directory scope:** Agent assumes `fbandroid/` when code is in `fbobjc/`
3. **Settling too early:** Finds plausible file, presents it without checking alternatives
4. **Query spiral:** 30-100+ queries trying minor variations instead of pivoting

### Eval Case Format (from P2255051401)

```yaml
id: cli-001
suite: code_search
description: 'Find: LumaResizeEventEmitter.js'
scenario: 'A developer needs to find a specific file...'
question: 'Where is the shared resize event emitter for Luma components?'

critical_requirements:
  - id: found_correct_file
    description: Response references the correct file path
    check: 'Response contains LumaResizeEventEmitter.js or full path'

quality_factors:
  - id: search_strategy
    description: Good query formulation, filters, narrowing
    weight: 0.34
  - id: tool_effectiveness
    description: Search tool returned useful results
    weight: 0.33
  - id: path_accuracy
    description: File path is complete and correct
    weight: 0.33

expected_answer: |
  xplat/js/RKJSModules/Libraries/Luma/components/resize/__private__/LumaResizeEventEmitter.js
```

### Key References

- **MSL Judge code:** `fbcode/msl/judge/clients/claude_code.py` — `ClaudeCodeAgentExecutor` + `ClaudeCodeJudgeClient`
- **mma_search config:** `fbsource/tools/devmate/evals/devai/mma_search_v2.yaml`
- **mma_search data:** `manifold://devai/tree/synthetic-benchmarks/code_retrieval_synthetic_benchmark_v2_1000.csv` (1000 cases, CSV with `filename` + `query`)
- **CLI source:** `fbsource/fbcode/codesearch/experimental_ucs_cli/src/main.rs`
- **Community post:** https://fb.workplace.com/groups/claude.code.community/permalink/935714322304022/

---

## 11. Plan: Import mma_search Cases + Add LLM-as-Judge

Based on the comparison with Aahan's approach, we're enhancing our eval harness:

1. **`scripts/import_mma_search.py`** — Download mma_search CSV from Manifold, convert to manifest format with `expected_answer`, `critical_requirements`, and `quality_factors` per case. Default: first 100 cases.
2. **`scripts/judge_results.py`** — Score collected results using `claude --print` (haiku model). Programmatic pass/fail checks (substring match for file path) + LLM quality factor scoring. Outputs `judged.json`.
3. **Update `compare_results.py`** — Add pass rate + quality score columns when judged data is available.
4. **Update `run_eval.sh`** — Add `--manifest` flag to select alternate case files (e.g., `cases/mma_search.json`).

Full implementation plan: `.claude/plans/reactive-noodling-shannon.md`
