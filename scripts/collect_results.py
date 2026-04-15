#!/usr/bin/env python3
"""
Collect and structure evaluation results from OpenCode sessions.

Usage:
  # From a JSONL output file (extracts session ID, parses events):
  python3 collect_results.py --jsonl /tmp/eval_dryrun/codesearch_q1.jsonl --query "Find TaoClient" --agent codesearch

  # From a session ID directly:
  python3 collect_results.py --session ses_abc123 --query "Find TaoClient" --agent codesearch

  # Export all results for a batch run:
  python3 collect_results.py --batch eval_manifest.json --output results.json
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def parse_jsonl(jsonl_path: str) -> dict:
    """Parse NDJSON events from an opencode run --format json output."""
    events = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not events:
        return {"events": [], "session_id": None, "error": "No events found in JSONL"}

    session_id = events[0].get("sessionID")
    first_ts = events[0].get("timestamp", 0)
    last_ts = events[-1].get("timestamp", 0)
    wall_time_ms = last_ts - first_ts if first_ts and last_ts else None

    # Extract tool calls from events
    tool_calls = []
    text_parts = []
    errors = []
    for ev in events:
        ev_type = ev.get("type")
        if ev_type == "tool_use":
            tool_calls.append({
                "tool": ev.get("tool", ev.get("name", "unknown")),
                "timestamp": ev.get("timestamp"),
            })
        elif ev_type == "text":
            text_parts.append(ev.get("text", ""))
        elif ev_type == "error":
            errors.append(ev.get("error", ev.get("message", "unknown error")))

    # Count tool calls by type
    tool_counts = {}
    for tc in tool_calls:
        tool_name = tc["tool"]
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

    final_text = text_parts[-1] if text_parts else ""

    return {
        "session_id": session_id,
        "wall_time_ms": wall_time_ms,
        "total_events": len(events),
        "tool_calls": [{"tool": k, "count": v} for k, v in sorted(tool_counts.items())],
        "total_tool_calls": len(tool_calls),
        "final_response": final_text,
        "errors": errors,
        "events": events,  # keep raw events for debugging
    }


def export_session(session_id: str) -> dict | None:
    """Run `opencode export <sessionID>` and return parsed JSON.

    Uses a temp file to avoid pipe buffer truncation (exports can be >64KB).
    """
    import tempfile
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.close()
        result = subprocess.run(
            f"opencode export {session_id} > {tmp.name}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        with open(tmp.name) as f:
            return json.load(f)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    finally:
        if tmp and os.path.exists(tmp.name):
            os.unlink(tmp.name)


def get_child_sessions(parent_id: str, db_path: str = None) -> list[str]:
    """Query SQLite for child (subagent) session IDs."""
    db = db_path or str(OPENCODE_DB)
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cursor = conn.execute(
            "SELECT id FROM session WHERE parent_id = ? ORDER BY time_created",
            (parent_id,),
        )
        return [row[0] for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def extract_session_metrics(session_data: dict) -> dict:
    """Extract metrics from an exported session JSON.

    Export format (from `opencode export`):
    - info: session metadata (id, title, time.created, time.updated, turnCount, etc.)
    - messages[]: each has info.role and parts[]
    - Part types:
      - "tool": tool call with .tool, .state.status, .state.input, .state.output, .state.time
      - "text": assistant text with .text
      - "step-finish": step completion with .cost, .tokens (input/output/reasoning/cache)
      - "step-start", "reasoning", "agent": metadata parts
    """
    info = session_data.get("info", {})
    messages = session_data.get("messages", [])

    tool_calls = []
    text_parts = []
    total_tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    total_cost = 0

    for msg in messages:
        role = msg.get("info", {}).get("role", "")
        if role != "assistant":
            continue
        for part in msg.get("parts", []):
            ptype = part.get("type", "")
            if ptype == "tool":
                state = part.get("state", {})
                tool_name = part.get("tool", "unknown")
                tool_time = state.get("time", {})
                start = tool_time.get("start", 0)
                end = tool_time.get("end", 0)
                tool_calls.append({
                    "tool": tool_name,
                    "status": state.get("status", "unknown"),
                    "duration_ms": (end - start) if start and end else None,
                    "input_preview": str(state.get("input", ""))[:200],
                })
            elif ptype == "text":
                text_parts.append(part.get("text", ""))
            elif ptype == "step-finish":
                total_cost += part.get("cost", 0)
                tokens = part.get("tokens", {})
                total_tokens["input"] += tokens.get("input", 0)
                total_tokens["output"] += tokens.get("output", 0)
                total_tokens["reasoning"] += tokens.get("reasoning", 0)
                cache = tokens.get("cache", {})
                total_tokens["cache_read"] += cache.get("read", 0)
                total_tokens["cache_write"] += cache.get("write", 0)

    # Count tools
    tool_counts = {}
    for tc in tool_calls:
        tool_counts[tc["tool"]] = tool_counts.get(tc["tool"], 0) + 1

    # Last assistant text is the final response
    final_response = text_parts[-1] if text_parts else ""

    # Timing
    time_info = info.get("time", {})
    created = time_info.get("created", 0)
    updated = time_info.get("updated", 0)
    wall_time_ms = updated - created if created and updated else None

    return {
        "session_id": info.get("id"),
        "title": info.get("title"),
        "wall_time_ms": wall_time_ms,
        "message_count": len(messages),
        "tool_calls_summary": [{"tool": k, "count": v} for k, v in sorted(tool_counts.items())],
        "tool_calls_detail": tool_calls,
        "total_tool_calls": len(tool_calls),
        "final_response": final_response,
        "turn_count": info.get("turnCount", 0),
        "tokens": total_tokens,
        "cost": total_cost,
    }


def collect_result(
    query: str,
    agent: str,
    session_id: str = None,
    jsonl_path: str = None,
) -> dict:
    """Collect full evaluation result for a single run."""
    result = {
        "query": query,
        "agent": agent,
        "session_id": session_id,
        "subagent_sessions": [],
        "parent_metrics": {},
        "subagent_metrics": [],
        "jsonl_metrics": {},
        "error": None,
    }

    # Parse JSONL if available
    if jsonl_path and os.path.exists(jsonl_path):
        jsonl_data = parse_jsonl(jsonl_path)
        result["jsonl_metrics"] = {
            k: v for k, v in jsonl_data.items() if k != "events"
        }
        if not session_id:
            session_id = jsonl_data.get("session_id")
            result["session_id"] = session_id

    if not session_id:
        result["error"] = "No session ID available"
        return result

    # Export parent session
    parent_data = export_session(session_id)
    if parent_data:
        result["parent_metrics"] = extract_session_metrics(parent_data)

    # Find and export child (subagent) sessions
    child_ids = get_child_sessions(session_id)
    result["subagent_sessions"] = child_ids

    for child_id in child_ids:
        child_data = export_session(child_id)
        if child_data:
            child_metrics = extract_session_metrics(child_data)
            child_metrics["child_session_id"] = child_id
            result["subagent_metrics"].append(child_metrics)

    return result


def run_batch(manifest_path: str, output_path: str):
    """Run collection for a batch of eval cases from a manifest file.

    Manifest format:
    {
      "cases": [
        {
          "id": 1,
          "query": "Find the definition of the TaoClient class",
          "explore_jsonl": "/tmp/eval/explore_q1.jsonl",
          "codesearch_jsonl": "/tmp/eval/codesearch_q1.jsonl",
          "explore_session": null,
          "codesearch_session": null
        }
      ]
    }
    """
    with open(manifest_path) as f:
        manifest = json.load(f)

    results = []
    for case in manifest["cases"]:
        query = case["query"]
        case_id = case.get("id", "?")
        print(f"Collecting case {case_id}: {query[:60]}...", file=sys.stderr)

        for agent in ["explore", "codesearch"]:
            jsonl_key = f"{agent}_jsonl"
            session_key = f"{agent}_session"
            r = collect_result(
                query=query,
                agent=agent,
                session_id=case.get(session_key),
                jsonl_path=case.get(jsonl_key),
            )
            r["case_id"] = case_id
            results.append(r)

    output = {"results": results, "total_cases": len(manifest["cases"])}

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {len(results)} results to {output_path}", file=sys.stderr)


def collect_from_run_dir(run_dir: str, manifest_path: str, output_path: str):
    """Collect results from a run directory produced by run_eval.sh.

    Scans for {agent}_q{N}.jsonl files and matches them against the manifest.
    """
    with open(manifest_path) as f:
        manifest = json.load(f)

    cases_by_id = {c["id"]: c for c in manifest["cases"]}

    # Load sessions.json if it exists
    sessions_file = os.path.join(run_dir, "sessions.json")
    sessions = {}
    if os.path.exists(sessions_file):
        with open(sessions_file) as f:
            sessions = json.load(f)

    results = []
    for case in manifest["cases"]:
        case_id = case["id"]
        query = case["query"]

        for agent in ["explore", "codesearch"]:
            jsonl_path = os.path.join(run_dir, f"{agent}_q{case_id}.jsonl")
            session_key = f"{agent}_q{case_id}"
            session_id = sessions.get(session_key)

            if not os.path.exists(jsonl_path) and not session_id:
                continue

            print(f"Collecting case {case_id} ({agent}): {query[:50]}...", file=sys.stderr)
            r = collect_result(
                query=query,
                agent=agent,
                session_id=session_id,
                jsonl_path=jsonl_path if os.path.exists(jsonl_path) else None,
            )
            r["case_id"] = case_id
            r["category"] = case.get("category", "unknown")
            results.append(r)

    output = {"results": results, "total_cases": len(manifest["cases"])}

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {len(results)} results to {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Collect OpenCode eval results")
    parser.add_argument("--jsonl", help="Path to JSONL output file from opencode run")
    parser.add_argument("--session", help="Session ID to export")
    parser.add_argument("--query", help="The eval query text", default="")
    parser.add_argument("--agent", help="Agent name (explore/codesearch)", default="unknown")
    parser.add_argument("--batch", help="Path to batch manifest JSON")
    parser.add_argument("--batch-from-run", help="Path to run directory (from run_eval.sh)")
    parser.add_argument("--manifest", help="Path to cases/manifest.json (for --batch-from-run)",
                        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                             "cases", "manifest.json"))
    parser.add_argument("--output", "-o", help="Output file path", default="-")

    args = parser.parse_args()

    if args.batch_from_run:
        output = args.output if args.output != "-" else os.path.join(args.batch_from_run, "collected.json")
        collect_from_run_dir(args.batch_from_run, args.manifest, output)
        return

    if args.batch:
        output = args.output if args.output != "-" else "eval_results.json"
        run_batch(args.batch, output)
        return

    result = collect_result(
        query=args.query,
        agent=args.agent,
        session_id=args.session,
        jsonl_path=args.jsonl,
    )

    output_str = json.dumps(result, indent=2)
    if args.output == "-":
        print(output_str)
    else:
        with open(args.output, "w") as f:
            f.write(output_str)
        print(f"Wrote result to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
