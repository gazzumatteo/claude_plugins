#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Deterministic parser for E2E test checklists.

Emits normalized JSON on stdout. Supports 4 shapes:
  - table:   | # | Step | Azione | Risultato Atteso | Pass |
  - prose:   H2/H3 sections with bash code blocks
  - nested:  - [ ] bullets under headings
  - cli:     prose without UI cues

Usage:
    python3 parse_checklist.py <path-to-checklist.md> [--out <full-json-path>]

Without --out: full normalized JSON goes to stdout (legacy behavior).
With --out:    full JSON is written to <full-json-path> and stdout receives
               only a compact summary (title, shape, counts, paths). Use this
               mode when the caller is an LLM orchestrator that must not pull
               the whole step list into its context window.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Step:
    id: str
    section: str
    action: str
    expected: str
    needs_browser: bool
    needs_cli: bool
    cli_commands: list[str] = field(default_factory=list)
    initial_status: str = "pending"
    destructive: bool = False


@dataclass
class Checklist:
    title: str
    source_path: str
    source_sha256: str
    prereqs: list[str] = field(default_factory=list)
    credentials_ref: str | None = None
    shape: str = "unknown"
    steps: list[Step] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

BROWSER_KEYWORDS = re.compile(
    r"\b(browser|portale|portal|portal\b|login|logout|click|naviga|navigate|"
    r"apri|open|redirect|url|pagina|page|wizard|form|modal|popup|toast|"
    r"dashboard|inbox|sidebar|navbar|link)\b",
    re.IGNORECASE,
)

CLI_KEYWORDS = re.compile(
    r"\b(curl|docker|ssh|npm|gh |kubectl|psql|sqlite3|systemctl|"
    r"bash|sh |restart|get /api|post /api|put /api|delete /api)\b",
    re.IGNORECASE,
)

DESTRUCTIVE_KEYWORDS = re.compile(
    r"\b(elimin|delet|drop|wipe|reset|purge|truncate|remove|rimuov|"
    r"cancel(?!lat)|clear|flush)\w*\b",
    re.IGNORECASE,
)

FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
TABLE_HEADER_RE = re.compile(r"^\s*\|.*\|.*\|\s*$", re.MULTILINE)
CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ xX\-~])\]\s+(.+)$", re.MULTILINE)
CHECKBOX_LINE_RE = re.compile(r"^\s*-\s*\[([ xX\-~])\]\s+(.+)$")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def extract_title(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def extract_prereqs(text: str) -> list[str]:
    """Extract lines under a 'Prerequisiti' / 'Prerequisites' section until next heading."""
    pattern = re.compile(
        r"^##+\s+(?:Prerequisiti|Prerequisites)\b.*?$(.*?)(?=^##+\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return []
    body = m.group(1)
    items: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("|") and set(line) <= {"|", "-", " ", ":"}:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            joined = " — ".join(c for c in cells if c)
            if joined:
                items.append(joined)
            continue
        items.append(line)
    return items


def find_credentials_ref(source_path: Path) -> str | None:
    """Look for a sibling or parent TESTING_CREDENTIALS.md file."""
    for candidate in (
        source_path.parent / "TESTING_CREDENTIALS.md",
        source_path.parent.parent / "TESTING_CREDENTIALS.md",
        source_path.parent / "CREDENTIALS.md",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def classify_step(action: str, expected: str) -> tuple[bool, bool, list[str]]:
    blob = f"{action}\n{expected}"
    needs_browser = bool(BROWSER_KEYWORDS.search(blob))
    needs_cli = bool(CLI_KEYWORDS.search(blob))
    commands: list[str] = []
    for lang, body in FENCE_RE.findall(blob):
        if lang.lower() in {"bash", "sh", "shell", "zsh", ""}:
            for ln in body.splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    commands.append(ln)
                    needs_cli = True
    return needs_browser, needs_cli, commands


def is_destructive(action: str, expected: str) -> bool:
    return bool(DESTRUCTIVE_KEYWORDS.search(f"{action}\n{expected}"))


def status_from_marker(ch: str) -> str:
    ch = ch.strip().lower()
    return {
        "": "pending",
        " ": "pending",
        "x": "pending",  # already-passed rows still re-run; final report is source of truth
        "-": "skipped",
        "~": "blocked",
    }.get(ch, "pending")


# ---------------------------------------------------------------------------
# Shape detectors / parsers
# ---------------------------------------------------------------------------

def detect_shape(text: str) -> str:
    table_hits = len(TABLE_HEADER_RE.findall(text))
    checkbox_hits = len(CHECKBOX_RE.findall(text))
    has_fences = bool(FENCE_RE.search(text))
    has_pass_column = bool(re.search(r"\|\s*(Pass|Status|Stato)\b", text, re.I))

    # Priority 1: nested wins when checkboxes dominate the file
    # (files with many `- [ ]` lines are checklists even if they include summary tables)
    if checkbox_hits >= max(10, table_hits * 3):
        return "nested"
    # Priority 2: step tables (need explicit Pass/Status column)
    if table_hits >= 3 and has_pass_column:
        return "table"
    # Priority 3: nested checkboxes (checklist-style, lower threshold)
    if checkbox_hits >= 5:
        return "nested"
    # Priority 3: prose or CLI (both have headings + optional fences)
    if has_fences and re.search(r"^##+\s", text, re.MULTILINE):
        cli_density = len(CLI_KEYWORDS.findall(text))
        browser_density = len(BROWSER_KEYWORDS.findall(text))
        return "cli" if cli_density > browser_density * 2 else "prose"
    if re.search(r"^##+\s", text, re.MULTILINE):
        return "prose"
    return "unknown"


def parse_table(text: str) -> list[Step]:
    """Parse step tables. A 'step table' MUST have a Pass/Status/Stato column —
    summary/prereq/credentials tables are skipped because they lack it."""
    steps: list[Step] = []
    current_section = ""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^##+\s+(.+?)\s*$", line)
        if heading:
            current_section = heading.group(1).strip()
            i += 1
            continue

        if line.strip().startswith("|"):
            header_line = line
            if i + 1 < len(lines) and re.match(r"^\s*\|[\s\|:\-]+\|\s*$", lines[i + 1]):
                headers = [c.strip().lower() for c in header_line.strip().strip("|").split("|")]
                col = {h: idx for idx, h in enumerate(headers)}
                pass_idx = next(
                    (col[k] for k in ("pass", "status", "stato") if k in col),
                    None,
                )

                # Skip tables that aren't step tables (no Pass column).
                if pass_idx is None:
                    j = i + 2
                    while j < len(lines) and lines[j].strip().startswith("|"):
                        j += 1
                    i = j
                    continue

                id_idx = col.get("#", 0)
                action_idx = next(
                    (col[k] for k in ("azione", "action", "step") if k in col),
                    1,
                )
                expected_idx = next(
                    (col[k] for k in (
                        "risultato atteso", "expected", "result", "risultato",
                    ) if k in col),
                    None,
                )

                j = i + 2
                while j < len(lines) and lines[j].strip().startswith("|"):
                    row_line = lines[j].strip()
                    cells = [c.strip() for c in row_line.strip("|").split("|")]
                    if len(cells) >= 2 and cells[id_idx]:
                        step_id = cells[id_idx] or f"{len(steps) + 1}"
                        action = cells[action_idx] if action_idx < len(cells) else ""
                        expected = cells[expected_idx] if expected_idx is not None and expected_idx < len(cells) else ""
                        marker = ""
                        if pass_idx is not None and pass_idx < len(cells):
                            m = re.search(r"\[([ xX\-~])\]", cells[pass_idx])
                            if m:
                                marker = m.group(1)
                        nb, nc, cmds = classify_step(action, expected)
                        steps.append(Step(
                            id=step_id,
                            section=current_section,
                            action=action,
                            expected=expected,
                            needs_browser=nb,
                            needs_cli=nc,
                            cli_commands=cmds,
                            initial_status=status_from_marker(marker),
                            destructive=is_destructive(action, expected),
                        ))
                    j += 1
                i = j
                continue
        i += 1
    return steps


def parse_prose_or_cli(text: str, default_needs_browser: bool) -> list[Step]:
    """Each H2/H3 (that is not a meta heading) becomes a step."""
    steps: list[Step] = []
    meta_headings = {
        "prerequisiti", "prerequisites", "context", "contesto",
        "riepilogo", "summary", "note", "notes", "legenda", "legend",
        "credenziali", "credentials",
    }
    sections = re.split(r"^(##+)\s+(.+?)\s*$", text, flags=re.MULTILINE)
    parent_section = ""
    current_h2 = ""
    i = 1
    idx = 0
    while i < len(sections) - 1:
        hashes = sections[i]
        heading = sections[i + 1].strip()
        body = sections[i + 2] if i + 2 < len(sections) else ""
        level = len(hashes)
        if level == 2:
            current_h2 = heading
        if heading.lower().split("—")[0].strip() in meta_headings or heading.lower().startswith(("prerequis", "credenzial", "credential", "contest", "context", "legend", "note", "summary", "riepil")):
            i += 3
            continue
        idx += 1
        step_id = f"{idx}"
        section_name = current_h2 if level > 2 and current_h2 and heading != current_h2 else heading
        action = heading
        expected = body.strip()
        nb, nc, cmds = classify_step(action, expected)
        if default_needs_browser and not nc:
            nb = True
        if not default_needs_browser and not nb:
            nc = nc or bool(cmds)
        steps.append(Step(
            id=step_id,
            section=section_name,
            action=action,
            expected=expected[:500] + ("…" if len(expected) > 500 else ""),
            needs_browser=nb,
            needs_cli=nc,
            cli_commands=cmds,
            initial_status="pending",
            destructive=is_destructive(action, expected),
        ))
        i += 3
    return steps


def parse_nested(text: str) -> list[Step]:
    steps: list[Step] = []
    current_section = ""
    lines = text.splitlines()
    idx = 0
    for line in lines:
        heading = re.match(r"^##+\s+(.+?)\s*$", line)
        if heading:
            current_section = heading.group(1).strip()
            continue
        cb = CHECKBOX_LINE_RE.match(line)
        if cb:
            marker = cb.group(1)
            content = cb.group(2).strip()
            if not content:
                continue
            idx += 1
            nb, nc, cmds = classify_step(content, "")
            steps.append(Step(
                id=f"{idx}",
                section=current_section,
                action=content,
                expected="",
                needs_browser=nb,
                needs_cli=nc,
                cli_commands=cmds,
                initial_status=status_from_marker(marker),
                destructive=is_destructive(content, ""),
            ))
    return steps


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse(path: Path) -> Checklist:
    text = path.read_text(encoding="utf-8")
    shape = detect_shape(text)
    checklist = Checklist(
        title=extract_title(text) or path.stem,
        source_path=str(path.resolve()),
        source_sha256=sha256_file(path),
        prereqs=extract_prereqs(text),
        credentials_ref=find_credentials_ref(path),
        shape=shape,
    )

    if shape == "table":
        checklist.steps = parse_table(text)
    elif shape == "nested":
        checklist.steps = parse_nested(text)
    elif shape == "cli":
        checklist.steps = parse_prose_or_cli(text, default_needs_browser=False)
    elif shape == "prose":
        checklist.steps = parse_prose_or_cli(text, default_needs_browser=True)
    else:
        checklist.steps = []

    return checklist


def main(argv: list[str]) -> int:
    args = argv[1:]
    out_path: Path | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--out":
            if i + 1 >= len(args):
                print("usage: parse_checklist.py <file> [--out <path>]", file=sys.stderr)
                return 2
            out_path = Path(args[i + 1])
            i += 2
            continue
        if a.startswith("--out="):
            out_path = Path(a.split("=", 1)[1])
            i += 1
            continue
        positional.append(a)
        i += 1

    if len(positional) != 1:
        print("usage: parse_checklist.py <file> [--out <path>]", file=sys.stderr)
        return 2
    path = Path(positional[0])
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 1

    checklist = parse(path)
    full = {
        "title": checklist.title,
        "source_path": checklist.source_path,
        "source_sha256": checklist.source_sha256,
        "prereqs": checklist.prereqs,
        "credentials_ref": checklist.credentials_ref,
        "shape": checklist.shape,
        "step_count": len(checklist.steps),
        "steps": [asdict(s) for s in checklist.steps],
    }

    if out_path is None:
        print(json.dumps(full, indent=2, ensure_ascii=False))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(full, indent=2, ensure_ascii=False), encoding="utf-8")

    destructive_count = sum(1 for s in checklist.steps if s.destructive)
    needs_browser_count = sum(1 for s in checklist.steps if s.needs_browser)
    needs_cli_count = sum(1 for s in checklist.steps if s.needs_cli)
    sections: list[str] = []
    seen: set[str] = set()
    for s in checklist.steps:
        if s.section and s.section not in seen:
            sections.append(s.section)
            seen.add(s.section)

    summary = {
        "out_path": str(out_path.resolve()),
        "title": checklist.title,
        "source_path": checklist.source_path,
        "source_sha256": checklist.source_sha256,
        "shape": checklist.shape,
        "step_count": len(checklist.steps),
        "destructive_count": destructive_count,
        "needs_browser_count": needs_browser_count,
        "needs_cli_count": needs_cli_count,
        "prereqs_count": len(checklist.prereqs),
        "credentials_ref": checklist.credentials_ref,
        "sections": sections[:20],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
