#!/usr/bin/env python3
"""Show exact case IDs and baseline failure signatures from change-verdict JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def preview(message: str, limit: int = 180) -> str:
    line = " ".join(message.splitlines()[0].split("\\n", 1)[0].split()) if message else ""
    return line[: limit - 1] + "…" if len(line) > limit else line


def inspect(result_path: Path) -> str:
    data = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "current" not in data or "baseline" not in data:
        raise ValueError("not a change-verdict result.json file")

    current = data["current"]
    baseline = data["baseline"]
    lines = [f"Verdict: {data.get('classification', 'unknown')}"]
    differences = data.get("environment_differences") or []
    if differences:
        lines.append("Config differences: " + ", ".join(differences))

    cases = (current.get("tests") or {}).get("case_ids") or []
    lines.append("Current case IDs:")
    lines.extend(f"  {case_id}" for case_id in cases)
    if not cases:
        lines.append("  (none; check the current log and JUnit report)")

    failures = baseline.get("failures") or []
    lines.append("Baseline failures:")
    for failure in failures:
        lines.append(f"  case: {failure.get('case_id', '')}")
        lines.append(f"  exception: {failure.get('exception') or '(unknown)'}")
        lines.append(f"  message: {preview(failure.get('message') or '')}")
    if not failures:
        lines.append("  (none; check baseline state and log)")

    lines.append(
        "Choose --expect-case from the exact case IDs above and a distinctive "
        "--expect-baseline phrase from the original symptom; inspect the full JUnit message first."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="Path to result.json from an initial comparison")
    args = parser.parse_args()
    try:
        print(inspect(args.result), end="")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"inspect_evidence: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
