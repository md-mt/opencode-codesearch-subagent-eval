#!/usr/bin/env python3
"""
Judge evaluation results using claude CLI as an LLM-as-Judge.

Scores each result on quality factors (relevance, specificity, completeness,
coherence). When expected_answer is available in the manifest, also does
programmatic pass/fail checking.

Usage:
  # Judge collected results (no ground truth — quality scoring only)
  python3 scripts/judge_results.py results/dryrun/collected.json

  # With manifest that has expected_answer fields
  python3 scripts/judge_results.py results/run1/collected.json --manifest cases/mma_search.json

  # Dry run — preview judge prompts without calling LLM
  python3 scripts/judge_results.py results/dryrun/collected.json --dry-run

  # Custom model
  python3 scripts/judge_results.py results/dryrun/collected.json --model sonnet
"""

import argparse
import json
import os
import subprocess
import sys
import time


QUALITY_FACTORS = [
    {
        "id": "relevance",
        "description": "Does the response directly address the question asked?",
        "weight": 0.25,
    },
    {
        "id": "specificity",
        "description": "Does the response cite specific file paths, class names, function signatures, or code snippets?",
        "weight": 0.30,
    },
    {
        "id": "completeness",
        "description": "Does the response cover the key aspects of the question (e.g., definition location, key methods, usage patterns)?",
        "weight": 0.25,
    },
    {
        "id": "coherence",
        "description": "Is the response well-organized, consistent, and free of contradictions or hallucinated paths?",
        "weight": 0.20,
    },
]


def build_judge_prompt(query: str, response: str, expected_answer: str | None = None) -> str:
    factors_text = "\n".join(
        f"- **{f['id']}** (weight {f['weight']}): {f['description']}"
        for f in QUALITY_FACTORS
    )

    expected_section = ""
    if expected_answer:
        expected_section = f"\n## Expected Answer\n{expected_answer}\n"

    return f"""You are evaluating a code search agent's response quality.

## Task
{query}
{expected_section}
## Agent's Response
{response}

## Quality Factors to Score
{factors_text}

Score each quality factor from 0.0 to 1.0. Return ONLY valid JSON with no markdown fencing:
{{
  "scores": {{
    "relevance": {{"score": 0.0, "reasoning": "..."}},
    "specificity": {{"score": 0.0, "reasoning": "..."}},
    "completeness": {{"score": 0.0, "reasoning": "..."}},
    "coherence": {{"score": 0.0, "reasoning": "..."}}
  }},
  "overall_reasoning": "1-2 sentence assessment"
}}"""


def check_critical_requirements(response: str, expected_answer: str) -> dict:
    """Programmatic pass/fail: check if expected file path appears in response."""
    basename = os.path.basename(expected_answer.strip())
    full_path = expected_answer.strip()

    passed = basename in response or full_path in response
    return {
        "found_correct_file": {
            "passed": passed,
            "matched": basename if basename in response else (full_path if full_path in response else None),
        }
    }


def call_judge(prompt: str, model: str = "haiku") -> dict | None:
    """Call claude --print to judge a response."""
    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", model,
    ]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("  TIMEOUT", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:200]}", file=sys.stderr)
        return None

    # Parse the claude --output-format json wrapper
    try:
        wrapper = json.loads(result.stdout)
        text = wrapper.get("result", result.stdout)
    except json.JSONDecodeError:
        text = result.stdout

    # Extract the JSON scores from the text
    # Strip markdown fencing if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        print(f"  Failed to parse judge response: {text[:200]}", file=sys.stderr)
        return None


def compute_overall_score(scores: dict) -> float:
    """Compute weighted average from quality factor scores."""
    total = 0.0
    weight_sum = 0.0
    for f in QUALITY_FACTORS:
        fid = f["id"]
        if fid in scores and isinstance(scores[fid], dict):
            total += scores[fid].get("score", 0) * f["weight"]
            weight_sum += f["weight"]
    return total / weight_sum if weight_sum > 0 else 0.0


def get_response(result: dict) -> str:
    """Extract the best available response from a result."""
    # Prefer parent final_response (what the user sees)
    pm = result.get("parent_metrics", {})
    response = pm.get("final_response", "")
    if response:
        return response

    # Fall back to subagent response
    subs = result.get("subagent_metrics", [])
    if subs:
        return subs[0].get("final_response", "")

    return ""


def judge_results(
    results_path: str,
    manifest_path: str | None = None,
    output_path: str | None = None,
    model: str = "haiku",
    dry_run: bool = False,
):
    with open(results_path) as f:
        data = json.load(f)

    results = data.get("results", [])

    # Load manifest for ground truth if available
    cases_by_id = {}
    if manifest_path and os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        cases_by_id = {c["id"]: c for c in manifest.get("cases", [])}

    judged_results = []
    total_judge_tokens = 0

    for i, result in enumerate(results):
        query = result.get("query", "")
        agent = result.get("agent", "?")
        case_id = result.get("case_id", "?")
        response = get_response(result)

        if not response:
            print(f"  Skipping case {case_id} ({agent}): no response", file=sys.stderr)
            result["judgment"] = {"error": "no response"}
            judged_results.append(result)
            continue

        # Check for ground truth
        case_def = cases_by_id.get(case_id, {})
        expected_answer = case_def.get("expected_answer")

        print(f"Judging case {case_id} ({agent}): {query[:60]}...", file=sys.stderr)

        # Build judge prompt
        prompt = build_judge_prompt(query, response, expected_answer)

        if dry_run:
            print(f"\n--- Judge prompt for case {case_id} ({agent}) ---", file=sys.stderr)
            print(prompt[:500] + "...\n", file=sys.stderr)
            result["judgment"] = {"dry_run": True}
            judged_results.append(result)
            continue

        # Programmatic pass/fail if we have ground truth
        critical_checks = None
        passed = None
        if expected_answer:
            critical_checks = check_critical_requirements(response, expected_answer)
            passed = all(c["passed"] for c in critical_checks.values())

        # LLM quality scoring
        judge_response = call_judge(prompt, model=model)

        if judge_response and "scores" in judge_response:
            scores = judge_response["scores"]
            overall_score = compute_overall_score(scores)
            judgment = {
                "passed": passed,
                "critical_checks": critical_checks,
                "quality_scores": scores,
                "overall_score": round(overall_score, 3),
                "overall_reasoning": judge_response.get("overall_reasoning", ""),
            }
        else:
            judgment = {
                "passed": passed,
                "critical_checks": critical_checks,
                "quality_scores": None,
                "overall_score": None,
                "error": "judge failed to return valid scores",
            }

        result["judgment"] = judgment
        judged_results.append(result)

        score_str = f"{judgment['overall_score']:.2f}" if judgment["overall_score"] is not None else "N/A"
        pass_str = f" pass={passed}" if passed is not None else ""
        print(f"  Score: {score_str}{pass_str}", file=sys.stderr)

        # Rate limit
        time.sleep(1)

    # Build summary
    scored = [r for r in judged_results if r.get("judgment", {}).get("overall_score") is not None]
    passed_results = [r for r in judged_results if r.get("judgment", {}).get("passed") is True]
    failed_results = [r for r in judged_results if r.get("judgment", {}).get("passed") is False]

    summary = {
        "total_results": len(judged_results),
        "scored": len(scored),
        "avg_quality_score": round(sum(r["judgment"]["overall_score"] for r in scored) / len(scored), 3) if scored else None,
    }
    if passed_results or failed_results:
        total_graded = len(passed_results) + len(failed_results)
        summary["pass_rate"] = round(len(passed_results) / total_graded, 3)
        summary["passed"] = len(passed_results)
        summary["failed"] = len(failed_results)

    # Per-agent summary
    agents = set(r.get("agent") for r in scored)
    per_agent = {}
    for agent in agents:
        agent_scored = [r for r in scored if r.get("agent") == agent]
        per_agent[agent] = {
            "count": len(agent_scored),
            "avg_quality_score": round(
                sum(r["judgment"]["overall_score"] for r in agent_scored) / len(agent_scored), 3
            ) if agent_scored else None,
        }
        agent_passed = [r for r in agent_scored if r.get("judgment", {}).get("passed") is True]
        agent_failed = [r for r in agent_scored if r.get("judgment", {}).get("passed") is False]
        if agent_passed or agent_failed:
            total = len(agent_passed) + len(agent_failed)
            per_agent[agent]["pass_rate"] = round(len(agent_passed) / total, 3)
    summary["per_agent"] = per_agent

    output = {
        "results": judged_results,
        "summary": summary,
    }

    output_str = json.dumps(output, indent=2)
    out_path = output_path or results_path.replace("collected.json", "judged.json")

    if out_path == "-":
        print(output_str)
    else:
        with open(out_path, "w") as f:
            f.write(output_str)
        print(f"\nWrote {len(judged_results)} judged results to {out_path}", file=sys.stderr)

    # Print summary table
    print(f"\n=== Judge Summary ===", file=sys.stderr)
    print(f"Total results: {summary['total_results']}", file=sys.stderr)
    if summary.get("avg_quality_score") is not None:
        print(f"Avg quality:   {summary['avg_quality_score']:.3f}", file=sys.stderr)
    if "pass_rate" in summary:
        print(f"Pass rate:     {summary['pass_rate']:.1%} ({summary['passed']}/{summary['passed']+summary['failed']})", file=sys.stderr)
    for agent, stats in per_agent.items():
        score_str = f"{stats['avg_quality_score']:.3f}" if stats.get("avg_quality_score") is not None else "N/A"
        line = f"  {agent}: quality={score_str}"
        if "pass_rate" in stats:
            line += f", pass_rate={stats['pass_rate']:.1%}"
        print(line, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Judge eval results with LLM-as-Judge")
    parser.add_argument("results", help="Path to collected.json")
    parser.add_argument("--manifest", help="Path to manifest with expected_answer fields")
    parser.add_argument("-o", "--output", help="Output path (default: judged.json alongside input)")
    parser.add_argument("--model", default="haiku", help="Claude model for judging (default: haiku)")
    parser.add_argument("--dry-run", action="store_true", help="Preview judge prompts without calling LLM")
    args = parser.parse_args()

    judge_results(
        args.results,
        manifest_path=args.manifest,
        output_path=args.output,
        model=args.model,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
