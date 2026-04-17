#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Post-run integrity audit for E2E test reports.

Checks:
  1. Every input step has an output row (no silent skip)
  2. Every PASS has evidence files that actually exist on disk
  3. Source file hash hasn't changed since run start
  4. Report does not contain fenced code blocks in "fix code" languages
  5. No rows where `observed` is a literal copy of `expected` (lazy paraphrase check)

Side effects:
  - Downgrades PASS -> UNVERIFIED where evidence missing
  - Marks run as FAILED if input/output step count mismatches
  - Rewrites both .json and .md with audit results appended

Usage:
    python3 audit_report.py <run-json> [--strict]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


FIX_CODE_FENCE_RE = re.compile(
    r"```(?:python|typescript|ts|javascript|js|tsx|jsx|go|rust|java|cpp|c\+\+|kotlin|swift)\b",
    re.IGNORECASE,
)

VALID_STATUSES = {"PASS", "FAIL", "BLOCKED", "UNVERIFIED", "SKIPPED", "PENDING"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def audit(run_json_path: Path, strict: bool = False) -> dict:
    data = json.loads(run_json_path.read_text(encoding="utf-8"))
    warnings: list[str] = []
    errors: list[str] = []
    downgraded = 0

    source = Path(data["source_path"])
    if source.exists():
        current_sha = sha256_file(source)
        if current_sha != data["source_sha256"]:
            errors.append(
                f"Source file modified during run (sha256 was {data['source_sha256'][:12]}, "
                f"now {current_sha[:12]})"
            )
    else:
        errors.append(f"Source file not found at {source}")

    expected_count = data["step_count"]
    actual_count = len(data["results"])
    if actual_count != expected_count:
        errors.append(
            f"Result row count mismatch: expected {expected_count}, got {actual_count}"
        )

    evidence_root = run_json_path.parent
    for row in data["results"]:
        status_raw = row.get("status", "pending")
        status = status_raw.upper() if isinstance(status_raw, str) else "pending"
        if status not in VALID_STATUSES:
            warnings.append(f"Step {row['id']}: invalid status '{status_raw}'")
            row["status"] = "UNVERIFIED"
            row["notes"] = (row.get("notes") or "") + f" [audit: invalid status '{status_raw}']"
            continue

        if status == "PASS":
            evidence = row.get("evidence") or []
            if not evidence:
                row["status"] = "UNVERIFIED"
                row["notes"] = (row.get("notes") or "") + " [audit: PASS downgraded — no evidence]"
                downgraded += 1
                continue
            missing = []
            for ev in evidence:
                ev_path = (evidence_root / ev) if not Path(ev).is_absolute() else Path(ev)
                if not ev_path.exists():
                    missing.append(ev)
            if missing:
                row["status"] = "UNVERIFIED"
                row["notes"] = (row.get("notes") or "") + f" [audit: evidence missing: {', '.join(missing)}]"
                downgraded += 1
                continue

            observed = (row.get("observed") or "").strip()
            expected = (row.get("expected") or "").strip()
            if observed and expected and observed == expected and len(observed) > 20:
                warnings.append(
                    f"Step {row['id']}: observed is a literal copy of expected — possible lazy paraphrase"
                )

    md_report_path = run_json_path.with_suffix(".md")
    if md_report_path.exists():
        md_text = md_report_path.read_text(encoding="utf-8")
        if FIX_CODE_FENCE_RE.search(md_text):
            warnings.append(
                "Report contains fenced code blocks in fix-code languages (python/ts/js/go/rust). "
                "Executor may have written code — inspect manually."
            )

    status_counts: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    for row in data["results"]:
        s = str(row.get("status", "pending")).upper()
        status_counts[s] = status_counts.get(s, 0) + 1

    verdict = "PASSED"
    if errors:
        verdict = "FAILED"
    elif status_counts.get("FAIL", 0) > 0:
        verdict = "BUGS_FOUND"
    elif status_counts.get("UNVERIFIED", 0) > 0 and strict:
        verdict = "UNVERIFIED"

    data["audit"] = {
        "ran": True,
        "verdict": verdict,
        "errors": errors,
        "warnings": warnings,
        "downgraded_count": downgraded,
        "status_counts": status_counts,
    }
    run_json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    if md_report_path.exists():
        md_text = md_report_path.read_text(encoding="utf-8")
        audit_section = render_audit_section(data["audit"])
        if "## Audit" in md_text:
            md_text = re.sub(r"## Audit.*?(?=\n##|\Z)", audit_section, md_text, flags=re.DOTALL)
        else:
            md_text = md_text.rstrip() + "\n\n" + audit_section + "\n"
        md_report_path.write_text(md_text, encoding="utf-8")

    return data["audit"]


def render_audit_section(audit_result: dict) -> str:
    lines = ["## Audit", "", f"**Verdict**: `{audit_result['verdict']}`", ""]
    counts = audit_result["status_counts"]
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    for key in ("PASS", "FAIL", "BLOCKED", "UNVERIFIED", "SKIPPED", "pending"):
        if counts.get(key, 0):
            lines.append(f"| {key} | {counts[key]} |")
    lines.append("")
    if audit_result["downgraded_count"]:
        lines.append(f"Downgraded PASS → UNVERIFIED: **{audit_result['downgraded_count']}** (missing evidence)")
        lines.append("")
    if audit_result["errors"]:
        lines.append("### Errors")
        for e in audit_result["errors"]:
            lines.append(f"- {e}")
        lines.append("")
    if audit_result["warnings"]:
        lines.append("### Warnings")
        for w in audit_result["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_json", help="Path to run JSON report")
    ap.add_argument("--strict", action="store_true",
                    help="Verdict UNVERIFIED if any unverified rows remain")
    args = ap.parse_args()

    path = Path(args.run_json)
    if not path.exists():
        print(f"run json not found: {path}", file=sys.stderr)
        return 1
    result = audit(path, strict=args.strict)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["verdict"] in ("PASSED",) else (2 if result["verdict"] == "FAILED" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
