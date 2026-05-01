# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai>=1.50.0",
#     "requests>=2.31.0",
#     "python-dotenv>=1.0.0",
# ]
# ///
"""Capability check for the local LM Studio model used as the e2e executor.

Three subtests, in order:
  T1. /v1/models reachable and target model loaded
  T2. Multimodal vision grounding on a real screenshot
  T3. Tool calling combined with vision in a single request

Configurable via env or sibling .env.local file:
  LMSTUDIO_BASE_URL   default http://127.0.0.1:1234/v1
  LMSTUDIO_MODEL      default nvidia/nemotron-3-nano-omni
  LMSTUDIO_API_KEY    default lm-studio (LM Studio ignores it)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

# Load .env.local (gitignored) from scripts/ — sibling of the production runner.
load_dotenv(Path(__file__).parent.parent / ".env.local")

BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
MODEL = os.environ.get("LMSTUDIO_MODEL", "nvidia/nemotron-3-nano-omni")
API_KEY = os.environ.get("LMSTUDIO_API_KEY", "lm-studio")
SCREENSHOT = Path(__file__).parent / "fixtures" / "playwright_home.png"

CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def banner(title: str) -> None:
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")


def strip_code_fence(text: str) -> str:
    return CODE_FENCE_RE.sub("", text.strip()).strip()


def encode_image(path: Path) -> str:
    return f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


@dataclass
class Subtest:
    name: str
    passed: bool
    detail: str = ""


def t1_models_endpoint() -> Subtest:
    banner("T1 — GET /v1/models")
    try:
        r = requests.get(f"{BASE_URL}/models", timeout=5)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        print(f"Models loaded: {ids}")
        if MODEL in ids:
            return Subtest("T1_models_endpoint", True, f"{MODEL} is loaded")
        return Subtest("T1_models_endpoint", False, f"{MODEL} not in loaded models")
    except Exception as exc:
        return Subtest("T1_models_endpoint", False, f"transport error: {exc}")


def t2_vision_grounding(client: OpenAI) -> Subtest:
    banner("T2 — multimodal vision grounding")
    if not SCREENSHOT.exists():
        return Subtest("T2_vision_grounding", False, f"missing fixture {SCREENSHOT}")

    expected_terms = ["get started", "playwright test", "playwright cli", "playwright mcp"]
    prompt = (
        "Look at the screenshot and answer in JSON only, no prose. "
        'Schema: {"primary_cta": string, "section_titles": string[], "visible_cli_commands": string[]}. '
        "primary_cta = exact text of the most prominent green call-to-action button. "
        "section_titles = the three card titles below the hero (left to right). "
        "visible_cli_commands = the three CLI install commands shown inside the cards."
    )

    rsp = client.chat.completions.create(
        model=MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": encode_image(SCREENSHOT)}},
            ],
        }],
        temperature=0.0,
        max_tokens=800,
    )
    raw = rsp.choices[0].message.content or ""
    print(f"Raw response:\n{raw}\n")

    try:
        parsed = json.loads(strip_code_fence(raw))
    except json.JSONDecodeError as exc:
        return Subtest("T2_vision_grounding", False, f"response not valid JSON: {exc}")

    flat = json.dumps(parsed).lower()
    hits = [t for t in expected_terms if t in flat]
    print(f"Expected terms found: {len(hits)}/{len(expected_terms)} -> {hits}")
    ok = len(hits) >= 3
    return Subtest(
        "T2_vision_grounding",
        ok,
        f"{len(hits)}/{len(expected_terms)} expected terms grounded",
    )


def t3_tool_calling(client: OpenAI) -> Subtest:
    banner("T3 — tool calling combined with vision")
    if not SCREENSHOT.exists():
        return Subtest("T3_tool_calling", False, f"missing fixture {SCREENSHOT}")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "click",
                "description": "Click a visible element. Use a unique CSS selector or visible text label.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector or unique visible text"},
                    },
                    "required": ["selector"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "type_text",
                "description": "Type text into an input field.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["selector", "value"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "assert_visible",
                "description": "Assert that the given text is visible on the current page.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
    ]

    goal = (
        "You are an e2e test agent. The user wants to begin the Playwright tutorial. "
        "Look at the screenshot and emit ONE tool call to perform the next correct action. "
        "Do not write prose; only emit a tool call."
    )

    rsp = client.chat.completions.create(
        model=MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": goal},
                {"type": "image_url", "image_url": {"url": encode_image(SCREENSHOT)}},
            ],
        }],
        tools=tools,
        tool_choice="auto",
        temperature=0.0,
        max_tokens=200,
    )
    msg = rsp.choices[0].message
    calls = msg.tool_calls or []
    print(f"Tool calls emitted: {len(calls)}")
    for c in calls:
        print(f"  - {c.function.name}({c.function.arguments})")
    if msg.content:
        print(f"Trailing content: {msg.content!r}")

    if not calls:
        return Subtest("T3_tool_calling", False, "no tool calls emitted")

    first = calls[0]
    try:
        args = json.loads(first.function.arguments)
    except json.JSONDecodeError as exc:
        return Subtest("T3_tool_calling", False, f"tool args not valid JSON: {exc}")

    sel = json.dumps(args).lower()
    sensible = any(token in sel for token in ["get started", "getstarted", "docs", "documentation", "tutorial"])
    detail = f"{first.function.name}({args}) — {'sensible' if sensible else 'odd'} target"
    return Subtest("T3_tool_calling", True, detail)


def main() -> int:
    print(f"BASE_URL = {BASE_URL}")
    print(f"MODEL    = {MODEL}")
    fixture_size = SCREENSHOT.stat().st_size if SCREENSHOT.exists() else 0
    print(f"FIXTURE  = {SCREENSHOT} ({fixture_size} bytes)")

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

    t1 = t1_models_endpoint()
    if not t1.passed:
        results = [t1]
    else:
        results = [t1, t2_vision_grounding(client), t3_tool_calling(client)]

    banner("VERDICT")
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name} — {r.detail}")
    all_pass = all(r.passed for r in results) and len(results) == 3
    print(f"\n=> {'GO' if all_pass else 'NO-GO'} for Option 1 (CLI esterno + slash command shim)")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
