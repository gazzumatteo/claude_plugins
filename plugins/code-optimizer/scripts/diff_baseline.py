#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Compare a fresh baseline.json against the original baseline.json and
report whether a regression was introduced.

Usage:
    diff_baseline.py <baseline.json> <current.json>

Exit codes:
    0 — no regression (status preserved or improved)
    1 — regression detected

Outputs a JSON verdict on stdout.

Rules for "regression":
  - any phase whose status was "ok" is now "failed" → regression
  - any phase whose exit_code increased → regression
  - any phase whose counts.errors or counts.failed increased → regression
  - new phases that were "skipped" remain skipped (no judgment)
  - improvements (failed → ok, fewer errors) are reported but never block
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def diff_phase(name: str, base: dict, curr: dict) -> dict:
    if base.get("status") == "skipped":
        return {"phase": name, "verdict": "skipped", "reason": "baseline skipped"}
    if curr.get("status") == "skipped":
        return {"phase": name, "verdict": "unavailable", "reason": "current skipped"}

    base_status = base.get("status")
    curr_status = curr.get("status")
    base_counts = base.get("counts", {}) or {}
    curr_counts = curr.get("counts", {}) or {}

    regressions: list[str] = []
    improvements: list[str] = []

    if base_status == "ok" and curr_status != "ok":
        regressions.append(f"status degraded: {base_status} -> {curr_status}")
    if base_status != "ok" and curr_status == "ok":
        improvements.append(f"status improved: {base_status} -> {curr_status}")

    for key in ("errors", "failed"):
        base_v = int(base_counts.get(key, 0) or 0)
        curr_v = int(curr_counts.get(key, 0) or 0)
        if curr_v > base_v:
            regressions.append(f"{key} count increased: {base_v} -> {curr_v}")
        elif curr_v < base_v:
            improvements.append(f"{key} count decreased: {base_v} -> {curr_v}")

    if base.get("exit_code") is not None and curr.get("exit_code") is not None:
        if (curr["exit_code"] or 0) > (base["exit_code"] or 0):
            regressions.append(f"exit_code rose: {base['exit_code']} -> {curr['exit_code']}")

    verdict = "regression" if regressions else ("improved" if improvements else "stable")
    return {
        "phase": name,
        "verdict": verdict,
        "regressions": regressions,
        "improvements": improvements,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: diff_baseline.py <baseline.json> <current.json>", file=sys.stderr)
        return 2
    base = load(Path(argv[1]))
    curr = load(Path(argv[2]))

    phases = ["lint", "typecheck", "test", "build"]
    results = []
    any_regression = False
    for name in phases:
        b = (base.get("phases") or {}).get(name) or {"status": "skipped"}
        c = (curr.get("phases") or {}).get(name) or {"status": "skipped"}
        d = diff_phase(name, b, c)
        results.append(d)
        if d["verdict"] == "regression":
            any_regression = True

    overall = "regression" if any_regression else (
        "improved" if any(r["verdict"] == "improved" for r in results) else "stable"
    )
    print(json.dumps({"verdict": overall, "phases": results}, indent=2))
    return 1 if any_regression else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
