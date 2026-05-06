"""Local executor for mobile-testing checklists.

Drives the iOS Simulator / Android Emulator under the control of a vision-capable
local model exposed by an OpenAI-compatible endpoint (LM Studio). Mirror of
`e2e-testing/scripts/e2e_local_runner.py` but talks to `mobile_mcp.backends`
directly — no MCP / stdio in the loop.

Run (must be invoked through the plugin venv so `mobile_mcp` is importable):
    uv run --directory plugins/mobile-testing python scripts/mobile_local_runner.py \\
        --device-id <udid> --checklist path/to/file.md

Verify config without running anything:
    uv run --directory plugins/mobile-testing python scripts/mobile_local_runner.py --check-config

Configuration cascade (lower lines override higher lines, except process env):
    1. Process env (LMSTUDIO_BASE_URL, LMSTUDIO_MODEL, LMSTUDIO_API_KEY)
    2. Per-project:  <project_root>/.mobile-testing.env
    3. Per-project:  <project_root>/.e2e-testing.env  ← fallback, LMSTUDIO_* keys only
                     (lets you reuse the e2e-testing plugin's project config)
    4. User-global:  $XDG_CONFIG_HOME/claude-mobile-testing/config.env
                     (default: ~/.config/claude-mobile-testing/config.env)
    5. Plugin-dev:   <plugin>/scripts/.env.local  (only when working in-tree)

Checklist format (prose, easiest):
    # Title

    ## Scenario 1: login flow
    - Tap the email field and type "user@example.com" (expected: caret appears in field)
    - Tap the password field and type "secret" (expected: dots appear)
    - Tap "Login" (expected: home screen visible)

A `(expected: …)` suffix is optional. When absent, the step passes as soon as
the action runs once without error.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import json
import os
import re
import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv
from openai import OpenAI

from mobile_mcp.backends.base import BackendBase
from mobile_mcp.backends.native.router import NativeBackendRouter

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni"
DEFAULT_API_KEY = "lm-studio"

PROTECTED_KEYS = ("LMSTUDIO_BASE_URL", "LMSTUDIO_MODEL", "LMSTUDIO_API_KEY")

LOOP_GUARD_THRESHOLD = 3
DEFAULT_MAX_ITERATIONS = 12
ASSERT_DEFAULT_TIMEOUT_MS = 2000


# ──────────────────────────── env cascade ────────────────────────────

def _user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claude-mobile-testing" / "config.env"


def _parse_dotenv_keys(path: Path) -> list[str]:
    keys: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return keys
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if k.isidentifier():
            keys.append(k)
    return keys


def load_env_cascade(project_root: Path) -> list[tuple[str, Path, list[str]]]:
    """Walk lowest-precedence to highest, layering env files. Process env always wins.

    `.e2e-testing.env` is read selectively (only LMSTUDIO_* keys propagate) so the
    user can share LM Studio config across plugins without leaking e2e-specific
    vars (PLAYWRIGHT_*, etc.) into mobile-testing's environment.
    """
    process_env_snapshot = {k: os.environ[k] for k in PROTECTED_KEYS if k in os.environ}
    sources: list[tuple[str, Path, list[str]]] = []
    # (level, path, protected_keys_only)
    candidates: list[tuple[str, Path, bool]] = [
        ("plugin-dev",          Path(__file__).parent / ".env.local",        False),
        ("user-global",         _user_config_path(),                         False),
        ("project-shared(e2e)", project_root / ".e2e-testing.env",           True),
        ("project-local",       project_root / ".mobile-testing.env",        False),
    ]
    for level, path, protected_only in candidates:
        if not path.exists():
            continue
        relevant = [k for k in _parse_dotenv_keys(path) if k in PROTECTED_KEYS]
        if protected_only:
            values = dotenv_values(path)
            for k in PROTECTED_KEYS:
                v = values.get(k)
                if v is not None:
                    os.environ[k] = v
        else:
            load_dotenv(path, override=True)
        sources.append((level, path, relevant))
    for k, v in process_env_snapshot.items():
        os.environ[k] = v
    if process_env_snapshot:
        sources.append(("process-env", Path("(shell)"), list(process_env_snapshot.keys())))
    return sources


# ──────────────────────────── checklist parser ────────────────────────────

_EXPECTED_RX = re.compile(r"\(expected:\s*(.+?)\)\s*$", re.IGNORECASE)


def parse_checklist(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Parse a markdown checklist into (title, steps).

    Recognized:
      - `# Title`              → checklist title (first H1)
      - `## Section`           → starts a new section
      - `- step description`   → one step. Optional `(expected: …)` trailing.
    """
    text = path.read_text(encoding="utf-8")
    title = path.stem
    section = ""
    steps: list[dict[str, Any]] = []
    counter = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip() or title
        elif stripped.startswith("## "):
            section = stripped[3:].strip()
        elif stripped.startswith("- "):
            body = stripped[2:].strip()
            if not body:
                continue
            counter += 1
            expected = ""
            m = _EXPECTED_RX.search(body)
            if m:
                expected = m.group(1).strip()
                body = _EXPECTED_RX.sub("", body).strip()
            steps.append({
                "id": str(counter),
                "section": section,
                "action": body,
                "expected": expected,
            })
    return title, steps


# ──────────────────────────── tools exposed to the model ────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "tap",
            "description": "Tap at device-pixel coordinates (x, y).",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the currently focused field. Tap the field first.",
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
            "name": "swipe",
            "description": "Swipe the screen. Use direction (up/down/left/right) or explicit from/to coords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "from_x": {"type": "integer"}, "from_y": {"type": "integer"},
                    "to_x": {"type": "integer"}, "to_y": {"type": "integer"},
                    "duration_ms": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a hardware-style key (home, back, enter, tab, escape, volume_up/down, recent_apps).",
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
            "name": "hide_keyboard",
            "description": "Dismiss the soft keyboard if open. No-op when no keyboard is visible.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "observe",
            "description": (
                "Get a structured UI snapshot. Use when the screenshot alone is ambiguous "
                "(only Appium backend returns a real ui_tree; native is limited)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "include": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Save a fresh screenshot to disk and return its path. Use sparingly — costs vision tokens.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dsl_batch",
            "description": (
                "Execute a list of DSL steps in one round-trip. Cheaper than calling individual tools when "
                "the next 2-5 actions are obvious. Each step is the same shape used by execute_dsl."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_step",
            "description": (
                "Terminate the current step. status='pass' when the expected outcome holds, 'fail' otherwise. "
                "List bugs you observed."
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


# ──────────────────────────── device-driving tools ────────────────────────────

class MobileTools:
    def __init__(self, backend: BackendBase, device_id: str, evidence_dir: Path):
        self.backend = backend
        self.device_id = device_id
        self.evidence_dir = evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._screenshot_idx = 0

    def _trace(self, payload: dict[str, Any]) -> None:
        with (self.evidence_dir / "trace.jsonl").open("a") as f:
            f.write(json.dumps(payload) + "\n")

    def _run(self, coro):
        return asyncio.run(coro)

    def take_screenshot(self) -> bytes | None:
        try:
            png = self._run(self.backend.get_screenshot(self.device_id, low_quality=True))
        except Exception as exc:  # noqa: BLE001
            self._trace({"event": "screenshot_failed", "error": f"{type(exc).__name__}: {exc}"})
            return None
        self._screenshot_idx += 1
        out = self.evidence_dir / f"screenshot_{self._screenshot_idx:02d}.png"
        out.write_bytes(png)
        return png

    def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool call. Returns (text_result, refresh_screenshot)."""
        try:
            if name == "tap":
                self._run(self.backend.tap(self.device_id, int(args["x"]), int(args["y"])))
                return (f"tapped at ({args['x']}, {args['y']})", True)
            if name == "type_text":
                self._run(self.backend.type_text(self.device_id, args["text"]))
                return (f"typed {args['text']!r}", True)
            if name == "swipe":
                direction = args.get("direction")
                if direction:
                    step = {"action": "swipe", "direction": direction,
                            "distance": args.get("distance", 300)}
                    if "duration_ms" in args:
                        step["duration_ms"] = args["duration_ms"]
                    self._run(self.backend.execute_dsl(self.device_id, [step]))
                    return (f"swiped {direction}", True)
                if "from_x" in args:
                    self._run(self.backend.swipe(
                        self.device_id, int(args["from_x"]), int(args["from_y"]),
                        int(args["to_x"]), int(args["to_y"]),
                        duration_ms=int(args.get("duration_ms", 300)),
                    ))
                    return (f"swiped ({args['from_x']},{args['from_y']}) → "
                            f"({args['to_x']},{args['to_y']})", True)
                return ("ERROR: swipe requires 'direction' or from/to coords.", False)
            if name == "press_key":
                self._run(self.backend.press_key(self.device_id, args["key"]))
                return (f"pressed key {args['key']!r}", True)
            if name == "hide_keyboard":
                self._run(self.backend.hide_keyboard(self.device_id))
                return ("keyboard hidden", True)
            if name == "observe":
                result = self._run(
                    self.backend.observe(self.device_id, include=args.get("include", ["ui_tree"]))
                )
                # Strip embedded base64 screenshot if any — it would blow up the response
                if isinstance(result, dict) and "screenshot" in result:
                    result = {k: v for k, v in result.items() if k != "screenshot"}
                text = json.dumps(result, indent=2)
                if len(text) > 8000:
                    text = text[:8000] + "\n... [truncated]"
                (self.evidence_dir / "observe.json").write_text(text)
                return (f"UI snapshot:\n{text}", False)
            if name == "screenshot":
                path = self._run(self.backend.save_screenshot(self.device_id))
                return (f"screenshot saved: {path}", True)
            if name == "dsl_batch":
                steps = args.get("steps") or []
                if not isinstance(steps, list):
                    return ("ERROR: dsl_batch expects 'steps' as a list.", False)
                result = self._run(self.backend.execute_dsl(self.device_id, steps))
                summary = {
                    "status": result.get("status"),
                    "step_count": result.get("step_count"),
                    "elapsed_ms": result.get("elapsed_ms"),
                    "errors": [
                        {"i": i, "action": s.get("action"), "error": s.get("error")}
                        for i, s in enumerate(result.get("step_results", []))
                        if s.get("status") in ("error", "failed")
                    ],
                }
                return (f"dsl_batch: {json.dumps(summary)}", True)
            return (f"ERROR: unknown tool {name!r}", False)
        except Exception as exc:  # noqa: BLE001
            return (f"ERROR: {type(exc).__name__}: {exc!s}", True)


# ──────────────────────────── tool-loop runner ────────────────────────────

def b64_image(png: bytes) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"},
    }


def trim_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "user" and isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    ]
    if len(image_indices) <= 1:
        return messages
    keep = image_indices[-1]
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i in image_indices and i != keep:
            text_parts = [p["text"] for p in m["content"] if p.get("type") == "text"]
            text = (text_parts[0] if text_parts else "") + " [screenshot dropped]"
            out.append({"role": "user", "content": text})
        else:
            out.append(m)
    return out


SYSTEM_PROMPT = (
    "You are a mobile end-to-end test agent driving an iOS Simulator or Android Emulator.\n"
    "Each step has an ACTION (what to do) and may have an EXPECTED outcome.\n"
    "Workflow on every iteration:\n"
    "  1. Look at the current screenshot.\n"
    "  2. Decide: does the EXPECTED outcome already hold? If YES → call finish_step('pass') IMMEDIATELY.\n"
    "  3. Otherwise emit ONE tool call (or `dsl_batch` for 2-5 obvious next steps in one shot).\n"
    "Rules:\n"
    "  - Only do what the ACTION asks. Do not explore for fun.\n"
    "  - Don't repeat the same call with the same args twice.\n"
    "  - Use observe (Appium) when the screenshot is ambiguous.\n"
    "  - Hide the keyboard before tapping a button below the input field on small screens."
)


def run_step(
    client: OpenAI,
    model: str,
    backend: BackendBase,
    device_id: str,
    step: dict[str, Any],
    out_dir: Path,
    max_iterations: int,
) -> StepResult:
    evidence = out_dir / f"step-{step['id']}"
    tools = MobileTools(backend, device_id, evidence)
    started = dt.datetime.now()

    initial_png = tools.take_screenshot()
    section_line = f"Section: {step['section']}\n" if step.get("section") else ""
    expected = (step.get("expected") or "").strip()
    if expected:
        expected_block = f"Expected outcome: {expected}\n"
    else:
        expected_block = (
            "Expected outcome: not stated. Treat the step as DONE as soon as the action ran once "
            "without error. Don't wait for visual change.\n"
        )
    screenshot_note = (
        "The current device screenshot is attached. Decide your next action."
        if initial_png is not None
        else "[screenshot capture failed — proceeding text-only]"
    )
    user_intro = (
        f"{section_line}"
        f"Step {step['id']}: {step['action']}\n"
        f"{expected_block}\n"
        f"{screenshot_note}"
    )
    if initial_png is not None:
        initial_content: list[dict[str, Any]] | str = [
            {"type": "text", "text": user_intro}, b64_image(initial_png),
        ]
    else:
        initial_content = user_intro
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": initial_content},
    ]

    final_status = "error"
    final_notes = ""
    final_bugs: list[dict[str, Any]] = []
    iterations_used = 0
    error: str | None = None
    last_signature: str | None = None
    repeat = 0

    for it in range(max_iterations):
        iterations_used = it + 1
        messages = trim_history(messages)
        try:
            rsp = client.chat.completions.create(
                model=model, messages=messages, tools=TOOL_SCHEMAS,
                tool_choice="auto", temperature=0.0, max_tokens=1024,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"chat.completions failed: {exc!s}"
            tools._trace({"iter": it, "error": error})
            break

        msg = rsp.choices[0].message
        tool_calls = msg.tool_calls or []
        tools._trace({
            "iter": it, "assistant_text": msg.content,
            "tool_calls": [{"name": tc.function.name, "args": tc.function.arguments} for tc in tool_calls],
        })

        if not tool_calls:
            final_status = "fail"
            final_notes = msg.content or "(no tool call and no content)"
            break

        first = tool_calls[0]
        sig = f"{first.function.name}|{first.function.arguments}"
        if first.function.name != "finish_step":
            if sig == last_signature:
                repeat += 1
            else:
                repeat = 1
                last_signature = sig
            if repeat >= LOOP_GUARD_THRESHOLD:
                tools._trace({"iter": it, "loop_guard": sig, "count": repeat})
                final_status = "fail"
                final_notes = (
                    f"loop guard: identical {first.function.name} call repeated "
                    f"{repeat}× without progress."
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
                tools._trace({"iter": it, "tool": "finish_step", "args": args})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "step terminated"})
                terminated = True
                break

            text, refresh = tools.execute(tc.function.name, args)
            tools._trace({"iter": it, "tool": tc.function.name, "args": args, "result": text[:600]})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
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
                        "Consider observe or finish_step('fail') if the device is unresponsive."
                    )
                messages.append({"role": "user", "content": nudge_content})

        if terminated:
            break
    else:
        final_status = "error"
        final_notes = f"max_iterations ({max_iterations}) exhausted without finish_step"

    duration = (dt.datetime.now() - started).total_seconds()
    return StepResult(
        id=step["id"], section=step.get("section", ""), action=step["action"],
        status=final_status, iterations=iterations_used, notes=final_notes,
        bugs=final_bugs, evidence_dir=str(evidence.relative_to(out_dir)),
        duration_s=duration, error=error,
    )


# ──────────────────────────── backend factory ────────────────────────────

def make_backend() -> BackendBase:
    backend_name = os.environ.get("MOBILE_BACKEND", "native")
    if backend_name == "appium":
        from mobile_mcp.backends.appium.backend import AppiumBackend
        return AppiumBackend(
            appium_url=os.environ.get("APPIUM_URL", "http://localhost:4723"),
            auto_start=os.environ.get("APPIUM_AUTO_START", "1") == "1",
        )
    return NativeBackendRouter()


# ──────────────────────────── reporting ────────────────────────────

def write_report(out_dir: Path, title: str, device_id: str, results: list[StepResult]) -> Path:
    payload = {
        "title": title,
        "device_id": device_id,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.status == "pass"),
            "failed": sum(1 for r in results if r.status == "fail"),
            "errors": sum(1 for r in results if r.status == "error"),
            "skipped": sum(1 for r in results if r.status == "skipped"),
        },
        "results": [asdict(r) for r in results],
    }
    report = out_dir / "report.json"
    report.write_text(json.dumps(payload, indent=2))
    return report


# ──────────────────────────── main ────────────────────────────

def cmd_check_config(sources: list[tuple[str, Path, list[str]]]) -> int:
    base_url = os.environ.get("LMSTUDIO_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("LMSTUDIO_MODEL", DEFAULT_MODEL)
    print(f"LMSTUDIO_BASE_URL = {base_url}")
    print(f"LMSTUDIO_MODEL    = {model}")
    print(f"MOBILE_BACKEND    = {os.environ.get('MOBILE_BACKEND', 'native')}")
    print(f"MOBILE_SCREENSHOT_DIR = {os.environ.get('MOBILE_SCREENSHOT_DIR', '(unset → tempdir)')}")
    print("\nEnv cascade (lowest precedence first):")
    if not sources:
        print("  (no env files found)")
    for level, path, keys in sources:
        keys_str = ", ".join(keys) if keys else "(no LMSTUDIO_* keys)"
        print(f"  [{level}] {path} → {keys_str}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--checklist", type=Path, help="Path to a markdown checklist")
    p.add_argument("--device-id", type=str, help="Device ID (UDID/serial). Required when --checklist is given.")
    p.add_argument("--out-dir", type=Path, help="Output directory for evidence + report.json")
    p.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--check-config", action="store_true")
    args = p.parse_args()

    sources = load_env_cascade(args.project_root)

    if args.check_config:
        return cmd_check_config(sources)

    if not args.checklist:
        print("error: --checklist FILE is required (or pass --check-config).", file=sys.stderr)
        return 2
    if not args.device_id:
        print("error: --device-id is required.", file=sys.stderr)
        return 2

    title, steps = parse_checklist(args.checklist)
    if not steps:
        print(f"error: no steps parsed from {args.checklist}", file=sys.stderr)
        return 2

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir or (Path(__file__).parent / "runs" / ts)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Default screenshots into the run folder unless caller already set the env.
    os.environ.setdefault("MOBILE_SCREENSHOT_DIR", str(out_dir / "screenshots"))

    base_url = os.environ.get("LMSTUDIO_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("LMSTUDIO_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("LMSTUDIO_API_KEY", DEFAULT_API_KEY)
    client = OpenAI(base_url=base_url, api_key=api_key)

    backend = make_backend()
    print(f"checklist: {title}  ({len(steps)} steps)")
    print(f"device:    {args.device_id}")
    print(f"out:       {out_dir}")
    print(f"endpoint:  {base_url} model={model}\n")

    try:
        asyncio.run(backend.start_bridge(args.device_id))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: start_bridge failed ({exc}); proceeding anyway — native backend may not need it.")

    results: list[StepResult] = []
    for step in steps:
        print(f"  ▶ step {step['id']}: {step['action'][:80]}")
        try:
            r = run_step(client, model, backend, args.device_id, step, out_dir, args.max_iterations)
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            (out_dir / f"step-{step['id']}-crash.txt").write_text(tb)
            r = StepResult(
                id=step["id"], section=step.get("section", ""), action=step["action"],
                status="error", error=f"{type(exc).__name__}: {exc!s}",
            )
        results.append(r)
        print(f"    {r.status}  ({r.iterations} iters, {r.duration_s:.1f}s)")
        # Always write report after each step so a crash doesn't lose evidence.
        write_report(out_dir, title, args.device_id, results)

    try:
        asyncio.run(backend.stop_bridge(args.device_id))
    except Exception:
        pass
    try:
        asyncio.run(backend.shutdown())
    except Exception:
        pass

    report = write_report(out_dir, title, args.device_id, results)
    summary = {r.status for r in results}
    print(f"\nreport: {report}")
    if "error" in summary or "fail" in summary:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
