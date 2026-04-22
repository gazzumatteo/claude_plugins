#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Initialize an E2E test run report.

Creates:
  <source_dir>/.e2e-runs/<basename>.<YYYY-MM-DD-HHMM>.json
  <source_dir>/.e2e-runs/<basename>.<YYYY-MM-DD-HHMM>.md
  <source_dir>/.e2e-runs/<basename>.<YYYY-MM-DD-HHMM>.evidence/
  <source_dir>/.e2e-runs/.current-source        (absolute path of source, read by hook)

Usage:
    python3 init_report.py <parsed-json> [--executor sonnet|haiku] [--browser chromium]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "report.md.tmpl"


def load_template() -> str:
    if not TEMPLATE_PATH.exists():
        return DEFAULT_TEMPLATE
    return TEMPLATE_PATH.read_text(encoding="utf-8")


DEFAULT_TEMPLATE = """# Run report — {title}

**Source**: `{source_path}`
**Ran at**: {ran_at}
**Executor**: {executor_model}
**Browser**: {browser}
**Source SHA256**: `{source_sha256}`

## Summary

| Status | Count |
|---|---|
| PASS | 0 |
| FAIL | 0 |
| BLOCKED | 0 |
| UNVERIFIED | 0 |
| SKIPPED | 0 |
| PENDING | {step_count} |

## Results

{results_table}

## Bugs

_No bugs reported yet._
"""


def render_results_table(steps: list[dict]) -> str:
    rows = ["| # | Section | Action | Status | Evidence | Notes |",
            "|---|---|---|---|---|---|"]
    for s in steps:
        action = s["action"].replace("|", r"\|").replace("\n", " ")[:120]
        section = s["section"].replace("|", r"\|")[:50]
        rows.append(f"| {s['id']} | {section} | {action} | pending | — | — |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("parsed_json", help="Path to parsed checklist JSON")
    ap.add_argument("--executor", default="claude-sonnet-4-6")
    ap.add_argument("--browser", default="chromium")
    ap.add_argument("--output-dir", default=None,
                    help="Override output dir (default: <source_dir>/.e2e-runs)")
    args = ap.parse_args()

    parsed = json.loads(Path(args.parsed_json).read_text(encoding="utf-8"))
    source_path = Path(parsed["source_path"])
    basename = source_path.stem
    now = datetime.now(timezone.utc).astimezone()
    timestamp = now.strftime("%Y-%m-%d-%H%M")

    output_dir = Path(args.output_dir) if args.output_dir else source_path.parent / ".e2e-runs"
    output_dir.mkdir(parents=True, exist_ok=True)

    report_stem = f"{basename}.{timestamp}"
    json_path = output_dir / f"{report_stem}.json"
    md_path = output_dir / f"{report_stem}.md"
    evidence_dir = output_dir / f"{report_stem}.evidence"
    evidence_dir.mkdir(exist_ok=True)

    run_json = {
        "run_id": report_stem,
        "source_path": str(source_path.resolve()),
        "source_sha256": parsed["source_sha256"],
        "ran_at": now.isoformat(),
        "executor_model": args.executor,
        "browser": args.browser,
        "title": parsed["title"],
        "prereqs": parsed.get("prereqs", []),
        "credentials_ref": parsed.get("credentials_ref"),
        "shape": parsed["shape"],
        "step_count": parsed["step_count"],
        "results": [
            {
                "id": s["id"],
                "section": s["section"],
                "action": s["action"],
                "expected": s["expected"],
                "needs_browser": s["needs_browser"],
                "needs_cli": s["needs_cli"],
                "cli_commands": s["cli_commands"],
                "destructive": s["destructive"],
                "status": "pending",
                "evidence": [],
                "notes": "",
                "started_at": None,
                "finished_at": None,
            }
            for s in parsed["steps"]
        ],
        "bugs": [],
        "audit": {"ran": False, "warnings": []},
    }
    json_path.write_text(json.dumps(run_json, indent=2, ensure_ascii=False), encoding="utf-8")

    template = load_template()
    md_content = template.format(
        title=parsed["title"],
        source_path=source_path,
        ran_at=now.isoformat(timespec="seconds"),
        executor_model=args.executor,
        browser=args.browser,
        source_sha256=parsed["source_sha256"],
        step_count=parsed["step_count"],
        results_table=render_results_table(parsed["steps"]),
    )
    md_path.write_text(md_content, encoding="utf-8")

    (output_dir / ".current-source").write_text(str(source_path.resolve()), encoding="utf-8")

    print(json.dumps({
        "json_report": str(json_path),
        "md_report": str(md_path),
        "evidence_dir": str(evidence_dir),
        "current_source_marker": str(output_dir / ".current-source"),
        "step_count": parsed["step_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
