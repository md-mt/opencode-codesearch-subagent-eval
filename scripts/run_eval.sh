#!/usr/bin/env bash
# Run evaluation cases for codesearch vs explore subagents.
#
# Usage:
#   ./scripts/run_eval.sh                        # Run all 24 cases, both agents
#   ./scripts/run_eval.sh --cases 1,2,3          # Run specific cases
#   ./scripts/run_eval.sh --agent codesearch     # Run one agent only
#   ./scripts/run_eval.sh --dir ~/fbsource       # Set working directory
#   ./scripts/run_eval.sh --run-id my-test       # Custom run ID (default: timestamp)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
MANIFEST="$REPO_DIR/cases/manifest.json"

# Defaults
CASES=""
AGENT=""
WORK_DIR="/data/users/$(whoami)/fbsource"
RUN_ID="run_$(date +%Y%m%d_%H%M%S)"

while [[ $# -gt 0 ]]; do
    case $1 in
        --cases) CASES="$2"; shift 2 ;;
        --agent) AGENT="$2"; shift 2 ;;
        --dir) WORK_DIR="$2"; shift 2 ;;
        --run-id) RUN_ID="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--cases 1,2,3] [--agent explore|codesearch] [--dir /path] [--run-id name]"
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

RESULTS_DIR="$REPO_DIR/results/$RUN_ID"
mkdir -p "$RESULTS_DIR"

# Determine which agents to run
if [[ -n "$AGENT" ]]; then
    AGENTS=("$AGENT")
else
    AGENTS=("explore" "codesearch")
fi

# Read case count
TOTAL_CASES=$(jq '.cases | length' "$MANIFEST")

# Build case ID list
if [[ -n "$CASES" ]]; then
    IFS=',' read -ra CASE_IDS <<< "$CASES"
else
    CASE_IDS=()
    for i in $(seq 1 "$TOTAL_CASES"); do
        CASE_IDS+=("$i")
    done
fi

echo "=== OpenCode Subagent Eval ===" >&2
echo "Run ID:     $RUN_ID" >&2
echo "Cases:      ${CASE_IDS[*]}" >&2
echo "Agents:     ${AGENTS[*]}" >&2
echo "Work dir:   $WORK_DIR" >&2
echo "Output dir: $RESULTS_DIR" >&2
echo "" >&2

# Sessions tracker
SESSIONS_FILE="$RESULTS_DIR/sessions.json"
echo '{}' > "$SESSIONS_FILE"

run_case() {
    local case_id=$1
    local agent=$2

    # Extract case data (0-indexed in jq)
    local idx=$((case_id - 1))
    local query
    query=$(jq -r ".cases[$idx].query" "$MANIFEST")
    local prompt
    prompt=$(jq -r ".cases[$idx].prompt" "$MANIFEST")

    # Construct the agent-specific prompt
    local full_prompt="Use the @${agent} agent to: ${prompt}"

    local jsonl_file="$RESULTS_DIR/${agent}_q${case_id}.jsonl"
    local stderr_file="$RESULTS_DIR/${agent}_q${case_id}.stderr"

    echo "[$(date +%H:%M:%S)] Case #${case_id} (${agent}): ${query}" >&2

    # Run opencode
    if opencode run "$full_prompt" \
        --format json \
        --yolo \
        --dir "$WORK_DIR" \
        > "$jsonl_file" \
        2> "$stderr_file"; then
        echo "[$(date +%H:%M:%S)] Case #${case_id} (${agent}): DONE" >&2
    else
        echo "[$(date +%H:%M:%S)] Case #${case_id} (${agent}): FAILED (exit $?)" >&2
    fi

    # Extract session ID from first JSONL event
    local session_id
    session_id=$(head -1 "$jsonl_file" 2>/dev/null | jq -r '.sessionID // empty' 2>/dev/null || echo "")
    if [[ -n "$session_id" ]]; then
        # Update sessions tracker (using a temp file to avoid race conditions)
        local tmp
        tmp=$(mktemp)
        jq --arg key "${agent}_q${case_id}" --arg val "$session_id" \
            '. + {($key): $val}' "$SESSIONS_FILE" > "$tmp" && mv "$tmp" "$SESSIONS_FILE"
    fi
}

# Run all cases sequentially
for case_id in "${CASE_IDS[@]}"; do
    for agent in "${AGENTS[@]}"; do
        run_case "$case_id" "$agent"
    done
done

echo "" >&2
echo "=== Eval Complete ===" >&2
echo "Results in: $RESULTS_DIR" >&2
echo "Sessions:   $SESSIONS_FILE" >&2
echo "" >&2
echo "Next steps:" >&2
echo "  python3 scripts/collect_results.py --batch-from-run $RESULTS_DIR -o $RESULTS_DIR/collected.json" >&2
echo "  python3 scripts/compare_results.py $RESULTS_DIR/collected.json" >&2
