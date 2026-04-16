# Executive Summary: Explore vs Codesearch on EdenFS

**Task:** T263476439 | **Diff:** D100719588 | **Date:** 2026-04-15

---

## A. Explore Agent Behavior on fbsource

The explore subagent is a grep/glob file search specialist with a 19-line generic prompt. It defines `"*": "deny"` permissions, allowing only grep, glob, bash, and read — no MCP tools. On small repos, this works as intended.

On fbsource, two factors override this design. First, `--yolo` mode (required for scripted/non-interactive use) appends `"*": "allow"` to the end of the permission chain. Because OpenCode resolves permissions with `findLast`, this overrides the agent's deny and grants access to all MCP tools — including `search_files` indexed search. Second, fbsource's root `.claude/CLAUDE.md` instructs the model: "NEVER use Grep or Glob... always use `search_files` MCP." This tells the model to avoid the very tools explore was built for.

The net effect: on fbsource, explore silently becomes a version of codesearch with identical MCP tool access but a weaker, generic prompt. Without MCP, explore either stalls on permission prompts or hallucinates tool calls — it cannot function on EdenFS. Its configured permissions and described behavior do not reflect what actually happens.

---

## B. Quantitative Evaluation and Next Steps

We built a custom eval harness and ran 24 cases across 6 categories (pinpoint lookup, cross-cutting search, architecture, API discovery, needle-in-haystack, broad exploration) with the explore agent on fbsource.

**Explore baseline (24 cases, with MCP):** avg subagent wall time 200.6s, avg 251K input tokens, avg 16.8 tool calls. Architecture queries were slowest (464.6s avg). **Dry run comparison (Case #1):** codesearch was 4.7x faster (56s vs 263s) and used 10x fewer tokens (5.2K vs 51K) — same tools, better prompt.

**Next steps:** Run the same 24 cases with codesearch for a full head-to-head comparison. To strengthen quality scoring, import the code search team's `mma_search` benchmark (1000 cases with ground truth file paths) and adopt their LLM-as-judge methodology using the MSL Judge framework (`fbcode/msl/judge/`), which scores search_strategy, tool_effectiveness, and path_accuracy.

---

## C. Recommendation

**Disable explore and route to codesearch on EdenFS repos.** D100719588 implements this.

On EdenFS, explore and codesearch have identical tool access (MCP indexed search). The only difference is prompt quality — codesearch's 7-step methodology produces faster, more token-efficient searches. Explore's identity as a grep/glob specialist is contradicted by both the permission system and repo-level instructions, making its behavior confusing and unpredictable.

The implementation follows CC@Meta's proven pattern: a redirect hook denies explore on EdenFS and instructs the model to use codesearch instead. The codesearch agent is defined in `managed-settings.json` with no core opencode changes. Non-EdenFS repos are unaffected — explore continues to work normally there.
