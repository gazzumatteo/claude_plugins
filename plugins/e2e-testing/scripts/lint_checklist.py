# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Lint a parsed E2E checklist for patterns that confuse the local runner.

The runner's failure modes observed in the field:
  • `max_iterations` exhausted on multi-action steps with no concrete EXPECTED.
  • Vague action verbs ("verifica", "check", "controlla") with no measurable outcome.
  • CLI steps where `cli_commands == []` because the action is in prose without a
    fenced ```bash block — the runner can't subprocess-run anything.
  • Click targets with very generic text ("Esci", "Salva", "OK") that match
    multiple elements in the page DOM.

Usage:
  python3 scripts/lint_checklist.py path/to/checklist.md
  python3 scripts/lint_checklist.py path/to/parsed.json    (already parsed)
  python3 scripts/lint_checklist.py path/to/checklist.md --json   (JSON output)

Exit codes: 0 = no findings, 1 = findings present (CI-friendly).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

VAGUE_VERBS = {
    "verifica", "verify", "controlla", "check", "test", "validate",
    "guarda", "look", "see", "vedi",
}
GENERIC_CLICK_TARGETS = {
    "ok", "salva", "save", "esci", "exit", "logout", "annulla", "cancel",
    "conferma", "confirm", "chiudi", "close", "indietro", "back", "avanti", "next",
    "submit", "invia", "ok",
}
CLI_HINT_RX = re.compile(
    r"\b(curl|wget|http|GET|POST|PUT|DELETE|PATCH|docker|kubectl|psql|"
    r"redis-cli|jq|grep|awk|find|ls\b|cat\b|tail|head|ssh|scp)\b",
    re.IGNORECASE,
)
ACTION_TOO_SHORT = 30  # actions shorter than this are probably under-specified


def _ensure_parsed(path: Path) -> dict:
    """Load a parsed-JSON file or run parse_checklist.py on a markdown file."""
    if path.suffix == ".json":
        return json.loads(path.read_text())
    parser = Path(__file__).parent / "parse_checklist.py"
    out = subprocess.run(
        ["python3", str(parser), str(path), "--out", "/tmp/lint-parsed.json"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(Path("/tmp/lint-parsed.json").read_text())


def _classify(step: dict) -> list[str]:
    """Return a list of finding tags for this step (empty if clean)."""
    findings: list[str] = []
    action = (step.get("action") or "").strip()
    expected = (step.get("expected") or "").strip()
    action_lc = action.lower()

    # 1. Missing expected — runner must guess "when am I done?"
    if not expected:
        findings.append("no-expected")

    # 2. Action too short / vague
    if len(action) < ACTION_TOO_SHORT:
        findings.append("action-too-short")
    first_word = (action_lc.split() or [""])[0].rstrip(":,.")
    if first_word in VAGUE_VERBS and not expected:
        findings.append("vague-verb-no-expected")

    # 3. CLI step with no extracted commands — parser couldn't find a fenced block.
    # Skip when needs_browser is also true: the CLI keyword is likely in `expected`
    # describing a server-side POST/GET that the UI triggers, not a command the
    # test should subprocess.
    if step.get("needs_cli") and not step.get("cli_commands") and not step.get("needs_browser"):
        findings.append("cli-no-commands")

    # 4. Looks like a CLI/HTTP step but tagged needs_browser only.
    if (
        not step.get("needs_cli")
        and CLI_HINT_RX.search(action) is not None
        and "click" not in action_lc
        and "click" not in (step.get("expected") or "").lower()
    ):
        findings.append("cli-hint-but-not-tagged")

    # 5. Generic click target — risk of selector ambiguity.
    # Only flag when the action is SHORT (no surrounding context to disambiguate).
    # `click "Salva"` alone is risky; `Sul modal X click "Salva" in basso` is fine.
    if step.get("needs_browser") and "click" in action_lc and len(action) < 80:
        m = re.search(r'click\s+["‘“]?([^"\n’”]{1,20})', action, re.I)
        if m:
            target = m.group(1).strip().strip(":,.").lower()
            if target in GENERIC_CLICK_TARGETS:
                findings.append(f"generic-click-target:{target}")

    return findings


def lint(parsed: dict) -> list[dict]:
    """Run all rules over every step. Returns one record per step with findings."""
    out: list[dict] = []
    for step in parsed.get("steps", []):
        tags = _classify(step)
        if tags:
            out.append({
                "id": step["id"],
                "section": step.get("section", ""),
                "action": step["action"],
                "findings": tags,
            })
    return out


def render_markdown(findings: list[dict], parsed: dict) -> str:
    if not findings:
        return f"# Checklist lint — {parsed.get('title', '?')}\n\n✅ No findings.\n"
    by_tag: dict[str, list[dict]] = {}
    for f in findings:
        for tag in f["findings"]:
            tag_root = tag.split(":")[0]
            by_tag.setdefault(tag_root, []).append(f)
    lines = [
        f"# Checklist lint — {parsed.get('title', '?')}",
        "",
        f"**{len(findings)} step(s) with findings** out of {parsed.get('step_count', '?')} total.",
        "",
    ]
    explanations = {
        "no-expected": "Step has no EXPECTED outcome — the local runner has to guess when the action is done. Add a concrete observable result.",
        "action-too-short": f"Action text is < {ACTION_TOO_SHORT} chars — likely under-specified. Spell out the steps the model should take.",
        "vague-verb-no-expected": "Action starts with a vague verb (verifica/check/...) AND has no EXPECTED. The runner has nothing measurable to compare against.",
        "cli-no-commands": "Step is tagged as needing CLI but the parser found no fenced ```bash block. Wrap the commands in ```bash ... ```.",
        "cli-hint-but-not-tagged": "Action text mentions HTTP / CLI tools (curl, GET, docker, ...) but no fenced ```bash block exists. Either add the block or rewrite the step as a browser action.",
        "generic-click-target": "Step says click a generic label (Esci/Salva/OK/...) — likely matches multiple elements. Use a more specific selector or add disambiguating context (e.g. \"the 'Salva' button in the user-edit modal\").",
    }
    for tag in sorted(by_tag.keys()):
        items = by_tag[tag]
        lines.append(f"## `{tag}` — {len(items)} step(s)")
        lines.append("")
        lines.append(f"_{explanations.get(tag, '')}_")
        lines.append("")
        for f in items[:50]:
            lines.append(f"- **{f['id']}** [{f['section']}] — {f['action'][:120]}")
        if len(items) > 50:
            lines.append(f"- _(... and {len(items) - 50} more)_")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, help="Path to a checklist .md or pre-parsed .json")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = ap.parse_args()

    parsed = _ensure_parsed(args.path)
    findings = lint(parsed)

    if args.json:
        json.dump({"title": parsed.get("title", ""), "findings": findings}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_markdown(findings, parsed))

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
