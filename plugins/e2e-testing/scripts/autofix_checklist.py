# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate a grep-friendly work-list from `lint_checklist.py` findings.

Reads a checklist (md or pre-parsed json), runs the linter, and emits
`<checklist>.todos.md` — one section per lint tag, one `- [ ]` checkbox per
finding with the source line number for fast grep / jump-to.

This tool is intentionally conservative: it does NOT modify the original
checklist. Fixing the findings still needs human judgment (an empty `expected`
column has no objectively-correct value the script could fill).

Usage:
  python3 scripts/autofix_checklist.py path/to/checklist.md
  python3 scripts/autofix_checklist.py path/to/checklist.md --out path/to/out.md
  python3 scripts/autofix_checklist.py path/to/checklist.md --tag action-too-short

Exit codes: 0 = no findings, 1 = findings written to todos file.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TAG_DESCRIPTIONS = {
    "no-expected": "**Add a non-empty `Risultato Atteso`.** The runner uses it as the EXPECTED outcome — empty cells force the model to guess termination and trigger `max_iterations exhausted`.",
    "action-too-short": "**Expand the `Azione` text to ≥30 chars** with page/section context. Bad: `Toggle ON`. Good: `On the Utility page, click the toggle next to 'OCR'`.",
    "vague-verb-no-expected": "**Action starts with a vague verb (verifica/check/...) and `expected` is empty.** Either rewrite the action to be a concrete instruction, or fill `expected` with the exact thing to look for.",
    "cli-no-commands": "**No fenced ```bash block AND no inline `\\`<binary>...\\`` recognized.** For HTTP: write `` `curl -fsS https://.../api/foo` ``. For shell: wrap in backticks starting with a known binary (curl, docker, jq, ...).",
    "cli-hint-but-not-tagged": "**Action mentions HTTP/CLI tools but the parser found no extractable command.** Wrap the actual command in backticks or a ```bash fence.",
    "generic-click-target": "**Click target is a generic label** (Esci/Salva/OK/...) likely matching multiple elements. Disambiguate with surrounding context or use a specific `data-testid`.",
}


def _ensure_parsed(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text())
    parser = Path(__file__).parent / "parse_checklist.py"
    subprocess.run(
        ["python3", str(parser), str(path), "--out", "/tmp/autofix-parsed.json"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(Path("/tmp/autofix-parsed.json").read_text())


def _line_index(md_path: Path) -> dict[str, int]:
    """Map `step_id` → 1-based line number where the action is in the source.

    Heuristic: for table rows, find lines starting with `| <id>` (allowing
    leading spaces). For prose/nested, find lines mentioning the id verbatim.
    Returns {} when the source isn't readable (e.g. JSON-only input).
    """
    if md_path.suffix == ".json":
        return {}
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, int] = {}
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            continue
        m = re.match(r"\|\s*([0-9][0-9A-Za-z.\-]*)\s*\|", stripped)
        if m and m.group(1) not in out:
            out[m.group(1)] = i
    return out


def render(findings: list[dict], parsed: dict, source_path: Path,
           line_idx: dict[str, int], tag_filter: str | None) -> str:
    by_tag: dict[str, list[dict]] = {}
    for f in findings:
        for tag in f["findings"]:
            tag_root = tag.split(":")[0]
            if tag_filter and tag_root != tag_filter:
                continue
            by_tag.setdefault(tag_root, []).append(f)

    lines = [
        f"# Checklist autofix — todos for `{source_path.name}`",
        "",
        f"Generated from `{source_path}`. Re-run after edits:",
        "",
        f"```bash",
        f"python3 plugins/e2e-testing/scripts/lint_checklist.py {source_path}",
        f"```",
        "",
        f"**{sum(len(v) for v in by_tag.values())} item(s)** across **{len(by_tag)} tag(s)**.",
        "",
    ]
    for tag in sorted(by_tag.keys()):
        items = by_tag[tag]
        lines.append(f"## `{tag}` — {len(items)} item(s)")
        lines.append("")
        lines.append(TAG_DESCRIPTIONS.get(tag, ""))
        lines.append("")
        for f in items:
            ln = line_idx.get(f["id"])
            ref = f"L{ln}" if ln else "no-line-info"
            section = f["section"] or "-"
            action = f["action"][:140].replace("\n", " ")
            lines.append(f"- [ ] **{f['id']}** ({ref}, {section}) — {action}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, help="Path to checklist.md or pre-parsed .json")
    ap.add_argument("--out", type=Path, default=None,
                    help="Where to write the todos. Default: <path>.todos.md")
    ap.add_argument("--tag", default=None,
                    help="Only include findings of this tag (e.g. action-too-short)")
    args = ap.parse_args()

    parsed = _ensure_parsed(args.path)
    # Run the linter via subprocess to keep this script independent of its internals.
    linter = Path(__file__).parent / "lint_checklist.py"
    rc = subprocess.run(
        ["python3", str(linter), str(args.path), "--json"],
        check=False, capture_output=True, text=True,
    )
    findings = json.loads(rc.stdout)["findings"] if rc.stdout else []

    if not findings:
        print(f"✅ no findings — nothing to do.")
        return 0

    line_idx = _line_index(args.path)
    out_md = render(findings, parsed, args.path, line_idx, args.tag)

    out_path = args.out or args.path.with_suffix(args.path.suffix + ".todos.md")
    out_path.write_text(out_md)
    n = out_md.count("- [ ]")
    print(f"📝 wrote {n} todo(s) to {out_path}")
    print(f"   tip: open it in your editor, fix top-down, re-run lint to track progress.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
