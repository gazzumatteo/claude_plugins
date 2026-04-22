#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Consolidate per-scanner Finding JSON files into a single findings.json and
a human-readable findings report.

Input:  a directory containing one JSON file per scanner (array of Findings).
Output: <out-dir>/findings.json (flat list) + <out-dir>/optimize-scan-report.md.

Usage:
    parse_findings.py <in-dir> <out-dir>

Each scanner emits an array of Finding dicts with the schema defined in
SKILL.md. This script:
  - Loads every *.json in <in-dir>
  - Validates the required fields, drops malformed entries (logged to stderr)
  - Assigns a stable global order (by category → id)
  - Emits the consolidated JSON + a markdown report grouped by category,
    with counts of severity / confidence / risk
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {
    "id", "category", "severity", "confidence", "files",
    "description", "proposed_action", "risk", "requires_manual_review",
}

CATEGORY_ORDER = [
    "deduplication",
    "type-consolidation",
    "dead-code",
    "circular-deps",
    "type-strengthening",
    "error-handling",
    "slop-removal",
    "complexity",
    "magic-constants",
    "naming",
    "excessive-parameters",
    "feature-envy",
    "god-class",
    "primitive-obsession",
]


def load_findings(in_dir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in sorted(in_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"! parse error in {path.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, list):
            print(f"! {path.name}: expected array, got {type(payload).__name__}", file=sys.stderr)
            continue
        for entry in payload:
            if not isinstance(entry, dict):
                print(f"! {path.name}: skipped non-object entry", file=sys.stderr)
                continue
            missing = REQUIRED_FIELDS - set(entry.keys())
            if missing:
                print(f"! {path.name}: dropped finding missing fields: {sorted(missing)}", file=sys.stderr)
                continue
            findings.append(entry)
    return findings


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cat_idx = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    return sorted(
        findings,
        key=lambda f: (cat_idx.get(f["category"], 999), f["id"]),
    )


def render_report(findings: list[dict[str, Any]]) -> str:
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in findings:
        by_cat[f["category"]].append(f)

    total = len(findings)
    sev = Counter(f["severity"] for f in findings)
    conf = Counter(f["confidence"] for f in findings)
    risk = Counter(f["risk"] for f in findings)
    review = sum(1 for f in findings if f.get("requires_manual_review"))

    lines: list[str] = []
    lines.append("# Code optimization scan report")
    lines.append("")
    lines.append(f"**Total findings**: {total}")
    lines.append("")
    lines.append("| Severity | Count |  | Confidence | Count |  | Risk | Count |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for key in ("high", "medium", "low"):
        lines.append(
            f"| {key} | {sev.get(key, 0)} |  | {key} | {conf.get(key, 0)} |  | {key} | {risk.get(key, 0)} |"
        )
    lines.append("")
    lines.append(f"**Requires manual review**: {review} / {total}")
    lines.append("")

    for cat in CATEGORY_ORDER:
        items = by_cat.get(cat, [])
        if not items:
            continue
        lines.append(f"## {cat} ({len(items)})")
        lines.append("")
        for f in items:
            review_tag = " [review]" if f.get("requires_manual_review") else ""
            files_str = ", ".join(f["files"][:3])
            if len(f["files"]) > 3:
                files_str += f", +{len(f['files']) - 3} more"
            lines.append(
                f"- **{f['id']}** — {f['description']}  "
                f"_sev:{f['severity']} conf:{f['confidence']} risk:{f['risk']}{review_tag}_"
            )
            lines.append(f"  - files: {files_str}")
            lines.append(f"  - action: {f['proposed_action']}")
            if f.get("reason_for_manual_review"):
                lines.append(f"  - review: {f['reason_for_manual_review']}")
        lines.append("")

    # categories not in CATEGORY_ORDER (unexpected)
    unknown_cats = set(by_cat.keys()) - set(CATEGORY_ORDER)
    for cat in sorted(unknown_cats):
        lines.append(f"## {cat} ({len(by_cat[cat])}) — unknown category")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: parse_findings.py <in-dir> <out-dir>", file=sys.stderr)
        return 2
    in_dir = Path(argv[1])
    out_dir = Path(argv[2])
    if not in_dir.is_dir():
        print(f"not a directory: {in_dir}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    findings = sort_findings(load_findings(in_dir))
    (out_dir / "findings.json").write_text(
        json.dumps(findings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "optimize-scan-report.md").write_text(render_report(findings), encoding="utf-8")

    print(json.dumps({
        "findings": len(findings),
        "categories": sorted({f["category"] for f in findings}),
        "output": {
            "json": str(out_dir / "findings.json"),
            "report": str(out_dir / "optimize-scan-report.md"),
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
