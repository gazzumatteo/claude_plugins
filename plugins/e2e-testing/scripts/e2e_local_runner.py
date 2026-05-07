# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai>=1.50.0",
#     "playwright>=1.45.0",
#     "pyyaml>=6.0",
# ]
# ///
"""Local executor for E2E test checklists.

Drives Chromium via Playwright (sync API) under the control of a vision-capable
local model exposed by an OpenAI-compatible endpoint (LM Studio). One agent
loop per checklist step, fresh per-step conversation, latest-image-only context
trimming, full evidence on disk.

Steps come from `parse_checklist.py` (sibling script that supports 4 markdown
shapes: table / prose / nested / cli). Without --checklist a 3-step inline smoke
runs against playwright.dev.

First-time setup:
    uv run --with playwright playwright install chromium

Run:
    uv run e2e_local_runner.py [--checklist FILE.md] [--headed] [--inject-error TOOLNAME]

Verify config without running anything:
    uv run e2e_local_runner.py --check-config [--project-root /path/to/project]

Configuration: a single `.testing.yml` in the project root (see scripts/config.py
for the schema). Process env LMSTUDIO_* vars override the YAML at runtime.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Sibling import (PEP 723 script — add scripts/ to sys.path before importing config).
sys.path.insert(0, str(Path(__file__).parent))
from config import (  # noqa: E402
    DEFAULT_LMSTUDIO_API_KEY as DEFAULT_API_KEY,
    DEFAULT_LMSTUDIO_BASE_URL as DEFAULT_BASE_URL,
    DEFAULT_LMSTUDIO_MODEL as DEFAULT_MODEL,
    CONFIG_FILENAME,
    ConfigError,
    Settings,
)

from openai import OpenAI  # noqa: E402
from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright  # noqa: E402

ACCESSIBILITY_SNAPSHOT_LIMIT = 8000
TOOL_TIMEOUT_MS = 8000
NAVIGATE_TIMEOUT_MS = 15000
ASSERT_TIMEOUT_MS = 2500
SCREENSHOT_TIMEOUT_MS = 10000  # hung pages should fail fast — don't wait Playwright's default 30s
LOOP_GUARD_THRESHOLD = 3  # auto-fail after N identical consecutive tool calls

SMOKE_CHECKLIST_TITLE = "smoke-inline-3-steps"
SMOKE_CHECKLIST_STEPS = [
    {
        "id": "1",
        "section": "Smoke",
        "action": "Navigate to https://playwright.dev/ and confirm the homepage loaded.",
        "expected": "Homepage is visible; the hero headline mentions 'Playwright enables reliable web automation'.",
        "needs_browser": True,
        "needs_cli": False,
        "destructive": False,
        "cli_commands": [],
    },
    {
        "id": "2",
        "section": "Smoke",
        "action": "Click the green 'Get started' call-to-action button on the homepage hero.",
        "expected": "Browser navigates away from the homepage to a docs page (URL contains '/docs').",
        "needs_browser": True,
        "needs_cli": False,
        "destructive": False,
        "cli_commands": [],
    },
    {
        "id": "3",
        "section": "Smoke",
        "action": "Verify the Installation section is visible on the page reached in step 2.",
        "expected": "The text 'Installation' is visible somewhere in the main content.",
        "needs_browser": True,
        "needs_cli": False,
        "destructive": False,
        "cli_commands": [],
    },
]

PARSER_SCRIPT = Path(__file__).parent / "parse_checklist.py"


def load_checklist(path: Path) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Run the deterministic parser as a subprocess and return (title, steps, full_payload)."""
    if not path.exists():
        raise FileNotFoundError(f"checklist not found: {path}")
    if not PARSER_SCRIPT.exists():
        raise FileNotFoundError(f"parser script missing: {PARSER_SCRIPT}")
    proc = subprocess.run(
        [sys.executable, str(PARSER_SCRIPT), str(path)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"parser failed (exit {proc.returncode}):\n{proc.stderr}")
    payload = json.loads(proc.stdout)
    return payload["title"], payload["steps"], payload

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Navigate the browser to a URL and wait for the DOM to be ready.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": (
                "Click a visible element. Prefer 'text' (the exact visible text label of the element). "
                "Use 'selector' only if no text is appropriate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Exact visible text label"},
                    "selector": {"type": "string", "description": "CSS / Playwright selector"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into an input. Identify the input by label, placeholder, or selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "placeholder": {"type": "string"},
                    "selector": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a keyboard key (e.g. 'Enter', 'Escape', 'Tab').",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accessibility_snapshot",
            "description": (
                "Get a structured text snapshot of the current page's accessibility tree. "
                "Useful when the screenshot alone is ambiguous or you need to find a hidden element."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assert_visible",
            "description": "Check whether a piece of text is currently visible on the page. Returns true/false.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_step",
            "description": (
                "Terminate the current step. Use status='pass' when the expected outcome matches what you "
                "see, 'fail' otherwise. List bugs you observed (visual glitches, wrong text, broken navigation, ...)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["pass", "fail"]},
                    "notes": {"type": "string"},
                    "bugs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                                "details": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["status"],
            },
        },
    },
]


@dataclass
class StepResult:
    id: str
    section: str
    action: str
    status: str  # pass | fail | error | skipped
    iterations: int = 0
    notes: str = ""
    bugs: list[dict[str, Any]] = field(default_factory=list)
    evidence_dir: str = ""
    duration_s: float = 0.0
    error: str | None = None


class BrowserTools:
    def __init__(self, page: Page, evidence_dir: Path, inject_error_on: str | None):
        self.page = page
        self.evidence_dir = evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._injected = inject_error_on

    def _trace(self, payload: dict[str, Any]) -> None:
        with (self.evidence_dir / "trace.jsonl").open("a") as f:
            f.write(json.dumps(payload) + "\n")

    def take_screenshot(self) -> bytes | None:
        """Capture viewport screenshot. Returns None on failure so callers can fall
        back to a text-only message — never let a screenshot timeout abort the run."""
        try:
            png = self.page.screenshot(type="png", full_page=False, timeout=SCREENSHOT_TIMEOUT_MS)
        except PWTimeout as exc:
            self._trace({"event": "screenshot_failed", "error": f"timeout: {exc}"})
            return None
        except Exception as exc:  # noqa: BLE001
            self._trace({"event": "screenshot_failed", "error": f"{type(exc).__name__}: {exc}"})
            return None
        (self.evidence_dir / "screenshot.png").write_bytes(png)
        return png

    def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool. Returns (text_result, refresh_screenshot)."""
        if self._injected == name:
            self._injected = None
            return ("ERROR (injected): tool failed for testing recovery — try a different approach.", True)
        try:
            if name == "navigate":
                self.page.goto(args["url"], wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT_MS)
                return (f"Navigated to {args['url']}. Page title: {self.page.title()!r}", True)
            if name == "click":
                text = args.get("text")
                selector = args.get("selector")
                if text:
                    locator = self.page.get_by_text(text, exact=False).first
                elif selector:
                    locator = self.page.locator(selector).first
                else:
                    return ("ERROR: click requires 'text' or 'selector'.", False)
                locator.click(timeout=TOOL_TIMEOUT_MS)
                return (f"Clicked element matching {{text:{text!r}, selector:{selector!r}}}", True)
            if name == "type_text":
                value = args["value"]
                if args.get("selector"):
                    self.page.locator(args["selector"]).first.fill(value, timeout=TOOL_TIMEOUT_MS)
                elif args.get("placeholder"):
                    self.page.get_by_placeholder(args["placeholder"]).first.fill(value, timeout=TOOL_TIMEOUT_MS)
                elif args.get("label"):
                    self.page.get_by_label(args["label"]).first.fill(value, timeout=TOOL_TIMEOUT_MS)
                else:
                    return ("ERROR: type_text requires one of selector/placeholder/label.", False)
                return (f"Typed {value!r} into the field.", True)
            if name == "press_key":
                self.page.keyboard.press(args["key"])
                return (f"Pressed key {args['key']!r}", True)
            if name == "accessibility_snapshot":
                snap = self.page.accessibility.snapshot() or {}
                text = json.dumps(snap, indent=2)
                truncated = text[:ACCESSIBILITY_SNAPSHOT_LIMIT]
                (self.evidence_dir / "snapshot.txt").write_text(text)
                suffix = "" if len(text) <= ACCESSIBILITY_SNAPSHOT_LIMIT else "\n... [truncated]"
                return (f"Accessibility tree:\n{truncated}{suffix}", False)
            if name == "assert_visible":
                target = args["text"]
                visible = self.page.get_by_text(target, exact=False).first.is_visible(timeout=ASSERT_TIMEOUT_MS)
                return (f"assert_visible({target!r}) -> {visible}", False)
            return (f"ERROR: unknown tool {name!r}", False)
        except PWTimeout as exc:
            return (f"ERROR: timeout — {exc!s}", True)
        except Exception as exc:  # noqa: BLE001
            return (f"ERROR: {type(exc).__name__}: {exc!s}", True)


def trim_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the most recent image-bearing user message intact; strip images from older ones."""
    image_indices: list[int] = []
    for i, m in enumerate(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            if any(part.get("type") == "image_url" for part in m["content"]):
                image_indices.append(i)
    if len(image_indices) <= 1:
        return messages
    keep = image_indices[-1]
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i in image_indices and i != keep:
            text_parts = [p["text"] for p in m["content"] if p.get("type") == "text"]
            text = (text_parts[0] if text_parts else "") + " [screenshot dropped from history]"
            out.append({"role": "user", "content": text})
        else:
            out.append(m)
    return out


def b64_image(png: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png).decode()}"}}


STRICT_VERDICT_TIMEOUT_S = 30
STRICT_VERDICT_MAX_TOKENS = 200

STRICT_VERDICT_SYSTEM = (
    "You are an independent verifier. You did NOT execute the test step — your only job is to "
    "look at the screenshot and decide whether the EXPECTED outcome is visibly confirmed.\n"
    "Reply with EXACTLY one of these words on the first line: YES, NO, UNCLEAR.\n"
    "Then on a new line, one short sentence justifying your verdict (≤ 30 words).\n"
    "  • YES = the screenshot clearly shows the expected outcome.\n"
    "  • NO  = the screenshot clearly contradicts the expected outcome.\n"
    "  • UNCLEAR = you cannot tell from this screenshot alone (loading, partial render, off-screen).\n"
    "Be strict. 'Probably ok' is UNCLEAR, not YES."
)


def _strict_verify(client: OpenAI, model: str, screenshot: bytes | None,
                   expected: str, action: str) -> tuple[str, str]:
    """Independent post-pass verification. Returns (verdict, justification).

    verdict ∈ {"YES", "NO", "UNCLEAR", "ERROR"}. ERROR means the verifier itself
    failed (network / parse) — caller should preserve the original pass and log.
    """
    if not expected.strip():
        return "UNCLEAR", "no EXPECTED text to verify against"
    if screenshot is None:
        return "UNCLEAR", "no fresh screenshot available"
    user_text = (
        f"ACTION the model just claimed to have completed: {action}\n"
        f"EXPECTED outcome to verify: {expected}\n"
        f"Reply YES / NO / UNCLEAR + one-line justification."
    )
    try:
        rsp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": STRICT_VERDICT_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    b64_image(screenshot),
                ]},
            ],
            temperature=0.0,
            max_tokens=STRICT_VERDICT_MAX_TOKENS,
            timeout=STRICT_VERDICT_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        return "ERROR", f"verifier call failed: {type(exc).__name__}"
    text = (rsp.choices[0].message.content or "").strip()
    if not text:
        return "ERROR", "verifier returned empty"
    first = text.split("\n", 1)[0].strip().rstrip(".,:").upper()
    rest = text.split("\n", 1)[1].strip() if "\n" in text else ""
    if first not in ("YES", "NO", "UNCLEAR"):
        # Sometimes the model embeds the keyword inside a sentence — try a substring scan.
        for kw in ("YES", "NO", "UNCLEAR"):
            if kw in first:
                first = kw
                break
        else:
            return "ERROR", f"verifier reply not parseable: {text[:80]}"
    return first, rest or text[:120]


def _format_auth_context(credentials: dict[str, dict[str, str]], base_url: str | None) -> str:
    """Build the credentials/base_url block injected into each step's system prompt.

    Only emit when there's something non-trivial to say. Returns "" otherwise so the
    base prompt stays compact for projects without credentials.
    """
    parts: list[str] = []
    if base_url:
        parts.append(f"Base URL for the application under test: {base_url}")
    if credentials:
        lines = ["", "Login credentials available for this run. When a step asks you to log in or authenticate:",
                 "  • If the step names a role (admin, reseller, user, ...), use that role.",
                 "  • If no role is named, default to 'admin'.",
                 "  • Use the email/password verbatim — do NOT invent values."]
        for role, fields in credentials.items():
            email = fields.get("email", "")
            password = fields.get("password", "")
            lines.append(f"  - {role}: email={email}, password={password}")
        # Auto-recovery: previous steps may have logged out, navigated away, or left the
        # session in an unexpected state. The runner does NOT auto-bootstrap between steps,
        # so the model has to handle this itself.
        lines.extend([
            "",
            "SESSION AUTO-RECOVERY (important for reliable runs):",
            "  • If at the START of a step you see a login screen instead of the expected page,",
            "    log in as 'admin' FIRST using the credentials above, THEN continue with the step.",
            "    Treat that login as setup — do NOT call finish_step('pass') after the login alone.",
            "  • If the step asks you to navigate somewhere protected and you land on the login",
            "    page, same rule: log in, then re-attempt the navigation.",
            "  • Do this transparently — it is not a test failure, it is recovering from prior-step state.",
        ])
        parts.append("\n".join(lines))
    return "\n".join(parts)


def run_step(
    client: OpenAI,
    model: str,
    page: Page,
    step: dict[str, Any],
    out_dir: Path,
    max_iterations: int,
    inject_error_on: str | None,
    auth_context: str = "",
    strict_verdict: bool = False,
) -> StepResult:
    evidence = out_dir / f"step-{step['id']}"
    tools = BrowserTools(page, evidence, inject_error_on)
    started = dt.datetime.now()

    initial_png = tools.take_screenshot()
    system = (
        "You are an end-to-end test agent. Each step has an ACTION (what to do) and an EXPECTED outcome.\n"
        "Workflow on every iteration:\n"
        "  1. Look at the current screenshot.\n"
        "  2. Decide: does the EXPECTED outcome already hold? If YES → call finish_step('pass') IMMEDIATELY "
        "with no additional tool calls. Extra clicks after success are forbidden — they pollute page state "
        "for subsequent steps.\n"
        "  3. Otherwise, emit ONE tool call to make progress.\n"
        "Rules:\n"
        "  - Only do what the ACTION asks. Do not explore the UI for fun.\n"
        "  - If the same tool fails twice with the same arguments, switch strategy "
        "(different selector, accessibility_snapshot, or finish_step('fail') with details).\n"
        "  - Never repeat a failing call unchanged."
    )
    if auth_context:
        system = system + "\n\n" + auth_context
    section_line = f"Section: {step['section']}\n" if step.get("section") else ""
    expected = (step.get("expected") or "").strip()
    if expected:
        expected_block = f"Expected outcome: {expected}\n"
    else:
        expected_block = (
            "Expected outcome: not stated explicitly. Treat this step as DONE as soon as the action "
            "has been performed once without error. Do NOT repeat the same action looking for a "
            "visible change — call finish_step('pass') after the first successful tool result.\n"
        )
    screenshot_note = (
        "The current page screenshot is attached. Decide your next action per the workflow."
        if initial_png is not None
        else "[screenshot capture failed — proceeding text-only; consider calling accessibility_snapshot]"
    )
    user_intro = (
        f"{section_line}"
        f"Step {step['id']}: {step['action']}\n"
        f"{expected_block}\n"
        f"{screenshot_note}"
    )
    initial_content: list[dict[str, Any]] | str
    if initial_png is not None:
        initial_content = [{"type": "text", "text": user_intro}, b64_image(initial_png)]
    else:
        initial_content = user_intro
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": initial_content},
    ]

    final_status = "error"
    final_notes = ""
    final_bugs: list[dict[str, Any]] = []
    iterations_used = 0
    error: str | None = None
    last_call_signature: str | None = None
    repeated_call_count = 0
    empty_recovery_used = False
    loop_recovery_used = False

    for iteration in range(max_iterations):
        iterations_used = iteration + 1
        messages = trim_history(messages)
        try:
            rsp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.0,
                max_tokens=1024,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"chat.completions failed: {exc!s}"
            tools._trace({"iter": iteration, "error": error})
            break

        msg = rsp.choices[0].message
        tool_calls = msg.tool_calls or []
        tools._trace({
            "iter": iteration,
            "assistant_text": msg.content,
            "tool_calls": [{"name": tc.function.name, "args": tc.function.arguments} for tc in tool_calls],
        })

        if not tool_calls:
            # C — empty-response recovery: one explicit nudge before failing.
            if not empty_recovery_used and not (msg.content or "").strip():
                empty_recovery_used = True
                tools._trace({"iter": iteration, "empty_recovery": "nudging once"})
                messages.append({
                    "role": "user",
                    "content": (
                        "You returned no tool call and no content. You MUST emit one of:\n"
                        "  • finish_step('pass') if the EXPECTED outcome already holds,\n"
                        "  • finish_step('fail', notes=...) if you cannot complete the step,\n"
                        "  • exactly one progress tool call (click / type / accessibility_snapshot / ...).\n"
                        "Do not respond with empty content again."
                    ),
                })
                continue
            final_status = "fail"
            final_notes = msg.content or "(no tool call and no content)"
            break

        # Loop guard: detect identical consecutive calls (model stuck in a rut).
        first_call = tool_calls[0]
        signature = f"{first_call.function.name}|{first_call.function.arguments}"
        if first_call.function.name != "finish_step":
            if signature == last_call_signature:
                repeated_call_count += 1
            else:
                repeated_call_count = 1
                last_call_signature = signature
            if repeated_call_count >= LOOP_GUARD_THRESHOLD:
                # B — loop-guard recovery: one nudge to switch strategy before failing.
                if not loop_recovery_used:
                    loop_recovery_used = True
                    tools._trace({"iter": iteration, "loop_recovery": "nudging once", "signature": signature})
                    repeated_call_count = 0
                    last_call_signature = None
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your last {LOOP_GUARD_THRESHOLD} `{first_call.function.name}` calls used "
                            f"identical arguments and made no progress. The selector you tried likely "
                            f"does not match the right element. Switch strategy now:\n"
                            f"  • Call `accessibility_snapshot` to see the actual element tree, OR\n"
                            f"  • Try a more specific selector (different role/text/aria-label), OR\n"
                            f"  • Call finish_step('fail', notes=...) explaining what you couldn't find.\n"
                            f"Do NOT repeat the same arguments again."
                        ),
                    })
                    continue
                tools._trace({"iter": iteration, "loop_guard_triggered": signature, "count": repeated_call_count})
                final_status = "fail"
                final_notes = (
                    f"loop guard: identical {first_call.function.name} call repeated "
                    f"{repeated_call_count}× without progress (after one recovery attempt) — auto-failed instead of looping."
                )
                break

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        terminated = False
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}

            if tc.function.name == "finish_step":
                final_status = args.get("status", "fail")
                final_notes = args.get("notes", "") or ""
                final_bugs = args.get("bugs") or []
                tools._trace({"iter": iteration, "tool": "finish_step", "args": args})
                # Strict-verdict: independent post-pass verification.
                # Only runs when the model claims pass AND the run was started with strict
                # mode. The verifier sees a fresh screenshot + the EXPECTED text only —
                # zero memory of the iteration loop, so false-positive passes get caught.
                if final_status == "pass" and strict_verdict:
                    fresh_png = tools.take_screenshot()
                    verdict, justification = _strict_verify(
                        client, model, fresh_png,
                        step.get("expected") or "",
                        step.get("action") or "",
                    )
                    tools._trace({"iter": iteration, "strict_verdict": verdict, "reason": justification})
                    if verdict == "NO":
                        final_status = "fail"
                        final_notes = (
                            f"STRICT VERIFIER OVERRIDE: model claimed pass but verifier disagreed. "
                            f"Verifier said: {justification}. Model notes: {final_notes}"
                        )
                    elif verdict == "UNCLEAR":
                        final_status = "error"
                        final_notes = (
                            f"STRICT VERIFIER could not confirm pass. "
                            f"Verifier said: {justification}. Model notes: {final_notes}"
                        )
                    # YES or ERROR (verifier itself failed) → keep original pass.
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "step terminated"})
                terminated = True
                break

            result_text, refresh = tools.execute(tc.function.name, args)
            tools._trace({"iter": iteration, "tool": tc.function.name, "args": args, "result": result_text[:600]})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
            if refresh:
                png = tools.take_screenshot()
                criterion = (step.get("expected") or step["action"])[:160]
                if png is not None:
                    nudge = (
                        f"Updated screenshot after {tc.function.name}. "
                        f"Re-check the success criterion (\"{criterion}\"). "
                        "If it now holds, call finish_step('pass'). Otherwise emit the next tool call."
                    )
                    nudge_content: list[dict[str, Any]] | str = [
                        {"type": "text", "text": nudge}, b64_image(png),
                    ]
                else:
                    nudge_content = (
                        f"Result of {tc.function.name} recorded; screenshot capture failed. "
                        f"Re-check the success criterion (\"{criterion}\"). "
                        "If it holds, call finish_step('pass'). Otherwise consider accessibility_snapshot "
                        "to inspect the page, or finish_step('fail') if the page is unresponsive."
                    )
                messages.append({"role": "user", "content": nudge_content})

        if terminated:
            break
    else:
        final_status = "error"
        final_notes = f"max_iterations ({max_iterations}) exhausted without finish_step"

    duration = (dt.datetime.now() - started).total_seconds()
    return StepResult(
        id=step["id"],
        section=step.get("section", ""),
        action=step["action"],
        status=final_status,
        iterations=iterations_used,
        notes=final_notes,
        bugs=final_bugs,
        evidence_dir=str(evidence.relative_to(out_dir)),
        duration_s=duration,
        error=error,
    )


CLI_COMMAND_TIMEOUT_S = 120
CLI_OUTPUT_LIMIT = 4000
CLI_STDERR_LIMIT = 2000


def run_cli_commands(commands: list[str], cwd: Path, timeout: int = CLI_COMMAND_TIMEOUT_S) -> list[dict[str, Any]]:
    """Run shell commands sequentially in `cwd`. Stops on first non-zero exit so the model
    evaluates a clean failure point instead of a cascade of unrelated errors."""
    results: list[dict[str, Any]] = []
    for cmd in commands:
        started = dt.datetime.now()
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(cwd),
                capture_output=True, text=True,
                timeout=timeout, errors="replace",
            )
            stdout, stderr = proc.stdout or "", proc.stderr or ""
            results.append({
                "cmd": cmd,
                "exit_code": proc.returncode,
                "stdout": stdout[:CLI_OUTPUT_LIMIT],
                "stdout_truncated": len(stdout) > CLI_OUTPUT_LIMIT,
                "stderr": stderr[:CLI_STDERR_LIMIT],
                "stderr_truncated": len(stderr) > CLI_STDERR_LIMIT,
                "duration_s": (dt.datetime.now() - started).total_seconds(),
            })
            if proc.returncode != 0:
                break
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            results.append({
                "cmd": cmd, "exit_code": -1,
                "stdout": (partial or "")[:CLI_OUTPUT_LIMIT],
                "stdout_truncated": len(partial or "") > CLI_OUTPUT_LIMIT,
                "stderr": f"timeout after {timeout}s",
                "stderr_truncated": False,
                "duration_s": float(timeout),
            })
            break
        except Exception as exc:  # noqa: BLE001
            results.append({
                "cmd": cmd, "exit_code": -2,
                "stdout": "", "stdout_truncated": False,
                "stderr": f"{type(exc).__name__}: {exc}",
                "stderr_truncated": False,
                "duration_s": (dt.datetime.now() - started).total_seconds(),
            })
            break
    return results


def format_cli_transcript(results: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for r in results:
        block = f"$ {r['cmd']}\n[exit {r['exit_code']}, {r['duration_s']:.2f}s]"
        if r["stdout"]:
            tag = " (truncated)" if r["stdout_truncated"] else ""
            block += f"\nSTDOUT{tag}:\n{r['stdout']}"
        if r["stderr"]:
            tag = " (truncated)" if r["stderr_truncated"] else ""
            block += f"\nSTDERR{tag}:\n{r['stderr']}"
        blocks.append(block)
    return "\n\n".join(blocks)


def run_cli_only_step(
    client: OpenAI,
    model: str,
    step: dict[str, Any],
    project_root: Path,
    out_dir: Path,
    auth_context: str = "",
) -> StepResult:
    """Execute a pure-CLI step: run cli_commands as subprocesses, then ask the model
    in a single turn whether the transcript matches the expected outcome.

    No vision, no browser. The only tool exposed is finish_step (tool_choice='required')."""
    evidence = out_dir / f"step-{step['id']}"
    evidence.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now()

    commands = step.get("cli_commands") or []
    if not commands:
        # Write the step's raw definition as evidence so the user can see exactly what
        # the parser saw and why it failed to extract commands. Most common causes:
        # unsupported language tag (only bash/sh/shell/zsh/no-tag are scanned), inline
        # backticks instead of fenced blocks, or commands described only in prose.
        (evidence / "step_definition.txt").write_text(
            "Action:\n"
            f"{step['action']}\n\n"
            "Expected:\n"
            f"{step.get('expected', '')}\n\n"
            "----\n"
            "The parser scans fenced code blocks tagged `bash`, `sh`, `shell`, `zsh`, "
            "or with no language tag. If your block uses a different tag (`console`, "
            "`yaml`, `dockerfile`, ...) or only inline backticks, the parser skips it. "
            "Re-tag the fence as ```bash or wrap the command with explicit fences."
        )
        duration = (dt.datetime.now() - started).total_seconds()
        return StepResult(
            id=step["id"], section=step.get("section", ""), action=step["action"],
            status="error", iterations=0,
            notes="parser extracted no CLI commands — see step_definition.txt evidence",
            evidence_dir=str(evidence.relative_to(out_dir)), duration_s=duration,
        )

    cli_results = run_cli_commands(commands, project_root)
    (evidence / "cli_results.json").write_text(json.dumps(cli_results, indent=2))
    transcript = format_cli_transcript(cli_results)
    (evidence / "transcript.txt").write_text(transcript)

    expected = (step.get("expected") or "").strip()
    expected_block = (
        f"Expected outcome: {expected}\n"
        if expected
        else "Expected outcome: not stated explicitly. Treat as pass when all commands exit 0 and "
             "the output is consistent with the stated action.\n"
    )
    section_line = f"Section: {step['section']}\n" if step.get("section") else ""

    finish_tool = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "finish_step")
    system = (
        "You assess the transcript of CLI commands run for an end-to-end test step. "
        "Reply ONLY by calling finish_step. No browser is involved.\n"
        "  - Pass when the commands ran successfully AND their output matches the expected outcome.\n"
        "  - Fail when an exit code is non-zero, the output reveals an error, or the expected outcome "
        "is contradicted by the observed output.\n"
        "  - Report bugs for genuine product defects (wrong values, broken integrations); do NOT report "
        "bugs about the test itself or about transient infra issues."
    )
    if auth_context:
        system = system + "\n\n" + auth_context
    user = (
        f"{section_line}"
        f"Step {step['id']}: {step['action']}\n"
        f"{expected_block}\n"
        f"Command transcript ({len(cli_results)} of {len(commands)} commands run; "
        f"chain stops on first non-zero exit):\n\n{transcript}\n\n"
        "Decide pass/fail and call finish_step with notes (and bugs if any)."
    )

    final_status = "error"
    final_notes = ""
    final_bugs: list[dict[str, Any]] = []
    error: str | None = None

    try:
        rsp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[finish_tool],
            tool_choice="auto",
            temperature=0.0,
            max_tokens=600,
        )
    except Exception as exc:  # noqa: BLE001
        error = f"chat.completions failed: {exc}"
    else:
        msg = rsp.choices[0].message
        tool_calls = msg.tool_calls or []
        # Persist the raw response for diagnostics — useful when tool_choice negotiation differs by server.
        (evidence / "model_response.json").write_text(json.dumps({
            "content": msg.content,
            "tool_calls": [
                {"name": tc.function.name, "arguments": tc.function.arguments}
                for tc in tool_calls
            ],
        }, indent=2))
        if tool_calls:
            tc = tool_calls[0]
            if tc.function.name == "finish_step":
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                final_status = args.get("status", "fail") or "fail"
                final_notes = (args.get("notes") or "").strip()
                final_bugs = args.get("bugs") or []
            else:
                error = f"unexpected tool: {tc.function.name}"
        else:
            # Fallback: some servers/models emit prose instead of a function call. Try to
            # extract a verdict from the text — pass if it explicitly says "pass" / matches
            # expected, fail otherwise.
            content = (msg.content or "").strip()
            if content:
                verdict = "pass" if "pass" in content.lower()[:200] and "fail" not in content.lower()[:200] else "fail"
                final_status = verdict
                final_notes = content[:600]
            else:
                error = "no tool call and empty content"
                final_notes = ""

    duration = (dt.datetime.now() - started).total_seconds()
    return StepResult(
        id=step["id"],
        section=step.get("section", ""),
        action=step["action"],
        status=("error" if error else final_status),
        iterations=1,
        notes=final_notes or (error or ""),
        bugs=final_bugs,
        evidence_dir=str(evidence.relative_to(out_dir)),
        duration_s=duration,
        error=error,
    )


def cleanup_page_state(page: Page) -> None:
    """Best-effort cleanup between steps: dismiss overlays/modals/dropdowns."""
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def make_skipped(step: dict[str, Any], reason: str) -> StepResult:
    return StepResult(
        id=step["id"],
        section=step.get("section", ""),
        action=step["action"],
        status="skipped",
        notes=reason,
    )


def resolve_out_dir(args: argparse.Namespace, project_root: Path, ts: str) -> Path:
    if args.out_dir:
        base = Path(args.out_dir).expanduser().resolve()
    elif args.checklist:
        base = Path(args.checklist).expanduser().resolve().parent / ".e2e-runs"
    else:
        base = project_root / ".e2e-runs"
    return base / ts


def cmd_check_config(project_root: Path, settings: Settings) -> int:
    print(f"Project root: {project_root}")
    print()
    if settings.source_path is None:
        print(f"  [not found] {CONFIG_FILENAME} — none of: project_root, $PWD, cwd")
    else:
        print(f"  [loaded   ] {settings.source_path}")
    print()
    print("Resolved values:")
    print(f"  executor          = {settings.executor}")
    print(f"  web.headed        = {settings.web.browser.headed}")
    print(f"  web.base_url      = {settings.web.base_url or '(unset)'}")
    print(f"  lmstudio.base_url = {settings.lmstudio.base_url}")
    print(f"  lmstudio.model    = {settings.lmstudio.model}")
    print(f"  lmstudio.api_key  = {'***set***' if settings.lmstudio.api_key and settings.lmstudio.api_key != DEFAULT_API_KEY else '(default)'}")
    print(f"  credentials       = {sorted(settings.credentials.keys()) if settings.credentials else '(none)'}")
    print()
    ok = settings.source_path is not None
    print(f"config_status={'ok' if ok else 'incomplete'}")
    if not ok:
        print()
        print(f"To fix: create {project_root / CONFIG_FILENAME}")
        print("  See plugins/e2e-testing/scripts/config.py for the schema.")
    return 0 if ok else 2


def main() -> int:
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--checklist", default=None,
                            help="Path to a checklist .md (default: built-in smoke).")
    arg_parser.add_argument("--project-root", default=None,
                            help="Project directory used for the per-project .testing.yml lookup "
                                 "and for the default --out-dir (default: current working directory).")
    arg_parser.add_argument("--out-dir", default=None,
                            help="Where to write run artefacts. Default: <checklist_dir>/.e2e-runs/ "
                                 "or <project_root>/.e2e-runs/ for the built-in smoke.")
    arg_parser.add_argument("--check-config", action="store_true",
                            help="Print the resolved .testing.yml config and exit (rc=0 ok, 2 incomplete).")
    arg_parser.add_argument("--headed", action="store_true",
                            help="Show the browser window. ORs with web.browser.headed in .testing.yml.")
    arg_parser.add_argument("--max-iterations", type=int, default=None,
                            help="Max model iterations per browser step. Default: run.max_iterations from .testing.yml (12 if unset).")
    arg_parser.add_argument("--inject-error", default=None,
                            help="Tool name whose first invocation fails on step 2 (one-shot)")
    arg_parser.add_argument("--allow-destructive", action="store_true",
                            help="Run steps marked destructive (default: skip)")
    arg_parser.add_argument("--strict-verdict", action="store_true", default=None,
                            help="After every model-claimed pass, run an independent LM-Studio verifier "
                                 "on a fresh screenshot. Downgrades to fail/error on disagreement. "
                                 "Default: run.strict_verdict from .testing.yml (false if unset).")
    arg_parser.add_argument("--only", default=None,
                            help="Comma-separated step ids to run (e.g. '1.1,1.2,2.3'); "
                                 "everything else is skipped with reason 'filtered by --only'.")
    arg_parser.add_argument("--from", dest="from_id", default=None,
                            help="Start from this step id inclusive — earlier steps are skipped. "
                                 "Useful to resume a long checklist after the previous run was cut short.")
    arg_parser.add_argument("--to", dest="to_id", default=None,
                            help="Stop after this step id inclusive — later steps are skipped.")
    args = arg_parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else Path.cwd().resolve()

    try:
        settings = Settings.load(project_root)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    settings.apply_lmstudio_env()
    # YAML headed flag ORs with --headed CLI override (CLI never disables YAML's true).
    if settings.web.browser.headed:
        args.headed = True
    # Same for run.auto_confirm_destructive: YAML true ORs with the CLI flag.
    if settings.run.auto_confirm_destructive:
        args.allow_destructive = True
    # CLI override > YAML > 12-default for max_iterations.
    if args.max_iterations is None:
        args.max_iterations = settings.run.max_iterations
    # CLI flag (--strict-verdict) > YAML (run.strict_verdict) > false default.
    if args.strict_verdict is None:
        args.strict_verdict = settings.run.strict_verdict

    if args.check_config:
        return cmd_check_config(project_root, settings)

    base_url = settings.lmstudio.base_url
    model = settings.lmstudio.model
    api_key = settings.lmstudio.api_key
    auth_context = _format_auth_context(settings.credentials, settings.web.base_url)

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = resolve_out_dir(args, project_root, ts)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.checklist:
        title, raw_steps, _ = load_checklist(Path(args.checklist))
        source = args.checklist
    else:
        title, raw_steps, source = SMOKE_CHECKLIST_TITLE, SMOKE_CHECKLIST_STEPS, "built-in"

    config_origin = str(settings.source_path) if settings.source_path else "defaults"
    print(f"Run dir   : {out_dir}")
    print(f"Model     : {model}")
    print(f"BaseURL   : {base_url}")
    print(f"Config    : {config_origin}")
    print(f"Checklist : {title}  ({len(raw_steps)} steps from {source})")
    print(f"Inject    : {args.inject_error or '(none)'}")

    client = OpenAI(base_url=base_url, api_key=api_key)

    only_set: set[str] | None = None
    if args.only:
        only_set = {s.strip() for s in args.only.split(",") if s.strip()}
    from_key = _step_key(args.from_id) if args.from_id else None
    to_key = _step_key(args.to_id) if args.to_id else None

    report_meta = {
        "started_at": ts,
        "model": model,
        "base_url": base_url,
        "checklist": title,
        "checklist_source": source,
        "config_origin": config_origin,
        "scope": {
            "only": sorted(only_set) if only_set else None,
            "from": args.from_id,
            "to": args.to_id,
        },
    }

    results: list[StepResult] = []
    fatal_error: str | None = None
    final_status = "complete"
    try:
        with sync_playwright() as p:
            browser = None
            page: Page | None = None
            try:
                for step in raw_steps:
                    skip_reason = _scope_skip_reason(step["id"], only_set, from_key, to_key)
                    if skip_reason:
                        print(f"\n--- Step {step['id']}: SKIP ({skip_reason})")
                        results.append(make_skipped(step, skip_reason))
                        _write_report(out_dir, report_meta, results, fatal_error=None, status="in_progress")
                        continue

                    r = _execute_step(
                        p, browser, page, step, args, client, model, project_root, out_dir,
                        auth_context, args.strict_verdict,
                    )
                    # _execute_step may have lazy-launched the browser — pick up the handles.
                    browser, page = r.browser, r.page
                    step_result = r.step_result
                    print(_format_step_log(step_result))
                    results.append(step_result)
                    # Incremental write: report.json reflects state after every step so a
                    # SIGINT / Bash-window timeout / hard signal never loses what we've done.
                    _write_report(out_dir, report_meta, results, fatal_error=None, status="in_progress")
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001
                        pass
    except KeyboardInterrupt:
        final_status = "interrupted"
        fatal_error = "interrupted by KeyboardInterrupt (Ctrl-C, SIGINT, or Bash window timeout)"
        print(f"\n[INTERRUPTED] {fatal_error} — flushing partial report", file=sys.stderr)
    except Exception:  # noqa: BLE001
        final_status = "crashed"
        fatal_error = traceback.format_exc()
        print(f"\n[FATAL] runner top-level crash captured into report.json:\n{fatal_error}", file=sys.stderr)

    # Final write — even if everything above blew up, this runs.
    summary = _write_report(out_dir, report_meta, results, fatal_error=fatal_error, status=final_status)
    print(f"\nReport    : {out_dir / 'report.json'}")
    print(f"Summary   : {summary}")
    print(f"Status    : {final_status}")
    if final_status != "complete":
        return 2
    return 0 if (summary["fail"] == 0 and summary["error"] == 0) else 1


def _step_key(step_id: str) -> tuple:
    """Convert a possibly-dotted step id ('1.2.10', '0.1', 'A.3') to a sortable key.
    Numeric parts sort before string parts at each level so '1.10' > '1.9'."""
    out = []
    for part in step_id.split("."):
        try:
            out.append((0, int(part)))
        except ValueError:
            out.append((1, part))
    return tuple(out)


def _scope_skip_reason(
    step_id: str,
    only_set: set[str] | None,
    from_key: tuple | None,
    to_key: tuple | None,
) -> str | None:
    if only_set is not None and step_id not in only_set:
        return "filtered by --only"
    if from_key is not None and _step_key(step_id) < from_key:
        return f"before --from {from_key}"
    if to_key is not None and _step_key(step_id) > to_key:
        return f"after --to {to_key}"
    return None


def _write_report(
    out_dir: Path,
    meta: dict[str, Any],
    results: list[StepResult],
    fatal_error: str | None,
    status: str,
) -> dict[str, int]:
    summary = {"total": len(results), "pass": 0, "fail": 0, "error": 0, "skipped": 0}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    report: dict[str, Any] = dict(meta)
    report["status"] = status
    report["summary"] = summary
    report["steps"] = [asdict(r) for r in results]
    if fatal_error:
        report["fatal_error"] = fatal_error
    # Atomic write so an external reader never sees a half-written JSON.
    tmp = out_dir / "report.json.tmp"
    tmp.write_text(json.dumps(report, indent=2))
    tmp.replace(out_dir / "report.json")
    return summary


@dataclass
class _ExecutedStep:
    step_result: StepResult
    browser: Any
    page: Page | None


def _execute_step(
    p: Any,
    browser: Any,
    page: Page | None,
    step: dict[str, Any],
    args: argparse.Namespace,
    client: OpenAI,
    model: str,
    project_root: Path,
    out_dir: Path,
    auth_context: str = "",
    strict_verdict: bool = False,
) -> _ExecutedStep:
    """Run one step with full crash containment. Any unhandled exception becomes a
    StepResult(status='error', error=traceback) so the for-loop never aborts and
    report.json always sees this step."""
    try:
        if step.get("destructive") and not args.allow_destructive:
            print(f"\n--- Step {step['id']}: SKIP (destructive; pass --allow-destructive to run)")
            return _ExecutedStep(
                make_skipped(step, "destructive — skipped (use --allow-destructive)"),
                browser, page,
            )

        if step.get("needs_cli") and not step.get("needs_browser"):
            print(f"\n--- Step {step['id']} [{step.get('section', '')}] (CLI): {step['action'][:80]}")
            r = run_cli_only_step(client, model, step, project_root, out_dir, auth_context)
            return _ExecutedStep(r, browser, page)

        # Browser path — lazy-launch Chromium on first browser step.
        if browser is None:
            browser = p.chromium.launch(headless=not args.headed)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()

        inj = args.inject_error if (args.inject_error and step["id"] == "2") else None
        print(f"\n--- Step {step['id']} [{step.get('section', '')}]: {step['action'][:80]}")
        cleanup_page_state(page)
        r = run_step(client, model, page, step, out_dir, args.max_iterations, inj, auth_context, strict_verdict)
        return _ExecutedStep(r, browser, page)
    except Exception as exc:  # noqa: BLE001
        evidence = out_dir / f"step-{step['id']}"
        evidence.mkdir(parents=True, exist_ok=True)
        tb = traceback.format_exc()
        (evidence / "crash.txt").write_text(tb)
        crashed = StepResult(
            id=step["id"],
            section=step.get("section", ""),
            action=step["action"],
            status="error",
            iterations=0,
            notes=f"runner crash: {type(exc).__name__}: {exc}"[:300],
            error=tb,
            evidence_dir=str(evidence.relative_to(out_dir)),
        )
        return _ExecutedStep(crashed, browser, page)


def _format_step_log(r: StepResult) -> str:
    line = f"  -> {r.status}  ({r.iterations} iters, {r.duration_s:.1f}s)"
    if r.notes:
        line += f"\n     notes: {r.notes[:200]}"
    if r.bugs:
        line += f"\n     bugs : {len(r.bugs)} reported"
    return line


if __name__ == "__main__":
    sys.exit(main())
