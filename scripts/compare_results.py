#!/usr/bin/env python3
"""
Compare evaluation results between codesearch and explore subagents.

Usage:
  python3 scripts/compare_results.py results/run1/collected.json
  python3 scripts/compare_results.py results/run1/collected.json --json -o summary.json
"""

import argparse
import json
import sys


def load_results(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def get_metrics(result: dict) -> dict:
    """Extract key metrics from a single result entry."""
    pm = result.get("parent_metrics", {})
    subs = result.get("subagent_metrics", [])

    # Use subagent metrics if available, otherwise parent
    if subs:
        sub = subs[0]
        wall_time = sub.get("wall_time_ms")
        tokens = sub.get("tokens", {})
        tool_calls = sub.get("total_tool_calls", 0)
        response_len = len(sub.get("final_response", ""))
    else:
        wall_time = pm.get("wall_time_ms")
        tokens = pm.get("tokens", {})
        tool_calls = pm.get("total_tool_calls", 0)
        response_len = len(pm.get("final_response", ""))

    total_tokens = tokens.get("input", 0) + tokens.get("output", 0)

    return {
        "wall_time_ms": wall_time,
        "total_tokens": total_tokens,
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "tool_calls": tool_calls,
        "response_len": response_len,
        "error": result.get("error"),
    }


def format_time(ms):
    if ms is None:
        return "N/A"
    return f"{ms / 1000:.1f}s"


def format_tokens(n):
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def compare(results_path: str, output_json: bool = False, output_path: str = None):
    data = load_results(results_path)
    results = data.get("results", [])

    # Group by case_id
    cases = {}
    for r in results:
        case_id = r.get("case_id", "?")
        agent = r.get("agent", "?")
        if case_id not in cases:
            cases[case_id] = {}
        cases[case_id][agent] = r

    # Print comparison table
    header = f"{'Case':>4}  {'Category':<20}  {'Codesearch':>20}  {'Explore':>20}  {'Winner':>12}"
    sep = "-" * len(header)
    print(header)
    print(sep)

    stats = {"codesearch_wins": 0, "explore_wins": 0, "ties": 0, "errors": 0}
    time_ratios = []
    token_ratios = []

    for case_id in sorted(cases.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
        case = cases[case_id]
        cs = case.get("codesearch")
        ex = case.get("explore")

        if not cs or not ex:
            continue

        cs_m = get_metrics(cs)
        ex_m = get_metrics(ex)

        # Determine category
        category = cs.get("query", "")[:18] if not hasattr(cs, "category") else ""
        # Try to get from manifest structure
        category = ""
        for r in [cs, ex]:
            q = r.get("query", "")
            if q:
                category = q[:18]
                break

        # Format cells
        cs_cell = f"{format_time(cs_m['wall_time_ms'])}/{format_tokens(cs_m['total_tokens'])}tok"
        ex_cell = f"{format_time(ex_m['wall_time_ms'])}/{format_tokens(ex_m['total_tokens'])}tok"

        # Determine winner (by wall time, lower is better)
        winner = "—"
        if cs_m["error"] or ex_m["error"]:
            winner = "ERROR"
            stats["errors"] += 1
        elif cs_m["wall_time_ms"] and ex_m["wall_time_ms"]:
            ratio = ex_m["wall_time_ms"] / cs_m["wall_time_ms"]
            if ratio > 1.2:
                winner = "codesearch"
                stats["codesearch_wins"] += 1
            elif ratio < 0.8:
                winner = "explore"
                stats["explore_wins"] += 1
            else:
                winner = "tie"
                stats["ties"] += 1
            time_ratios.append(ratio)

        if cs_m["total_tokens"] and ex_m["total_tokens"]:
            token_ratios.append(ex_m["total_tokens"] / cs_m["total_tokens"])

        print(f"#{case_id:>3}  {category:<20}  {cs_cell:>20}  {ex_cell:>20}  {winner:>12}")

    print(sep)
    total = stats["codesearch_wins"] + stats["explore_wins"] + stats["ties"]
    print(f"\nCodesearch wins: {stats['codesearch_wins']}/{total}")
    print(f"Explore wins:    {stats['explore_wins']}/{total}")
    print(f"Ties:            {stats['ties']}/{total}")
    if stats["errors"]:
        print(f"Errors:          {stats['errors']}")

    if time_ratios:
        avg_ratio = sum(time_ratios) / len(time_ratios)
        print(f"\nAvg time ratio (explore/codesearch): {avg_ratio:.2f}x")
    if token_ratios:
        avg_token_ratio = sum(token_ratios) / len(token_ratios)
        print(f"Avg token ratio (explore/codesearch): {avg_token_ratio:.2f}x")

    summary = {
        "total_cases": total,
        "codesearch_wins": stats["codesearch_wins"],
        "explore_wins": stats["explore_wins"],
        "ties": stats["ties"],
        "errors": stats["errors"],
        "avg_time_ratio": sum(time_ratios) / len(time_ratios) if time_ratios else None,
        "avg_token_ratio": sum(token_ratios) / len(token_ratios) if token_ratios else None,
    }

    if output_json:
        out = json.dumps(summary, indent=2)
        if output_path and output_path != "-":
            with open(output_path, "w") as f:
                f.write(out)
            print(f"\nSummary written to {output_path}", file=sys.stderr)
        else:
            print(f"\n{out}")


def main():
    parser = argparse.ArgumentParser(description="Compare codesearch vs explore eval results")
    parser.add_argument("results", help="Path to collected results JSON")
    parser.add_argument("--json", action="store_true", help="Also output summary as JSON")
    parser.add_argument("-o", "--output", help="Output path for JSON summary", default="-")
    args = parser.parse_args()

    compare(args.results, output_json=args.json, output_path=args.output)


if __name__ == "__main__":
    main()
