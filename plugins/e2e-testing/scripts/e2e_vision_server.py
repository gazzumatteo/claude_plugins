#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.0",
#     "openai>=1.50.0",
# ]
# ///
"""MCP server: vision-as-a-service for the e2e-testing plugin.

Exposes one tool: `analyze_image(path, prompt)`. Reads an image from disk, sends
it together with the prompt to a local OpenAI-compatible endpoint (LM Studio),
returns the model's text reply (~30-200 tokens).

The whole point: let the orchestrating Claude session ask "what's on this
screenshot?" without paying ~1.8k vision tokens per Read(png). Pair it with
Playwright MCP's `browser_take_screenshot(filename=...)` — Playwright saves the
PNG to disk, then `analyze_image` reads it and returns text only.

Configuration (read from process env at call time — no .env file parsing here):
    LMSTUDIO_BASE_URL  default http://127.0.0.1:1234/v1
    LMSTUDIO_MODEL     default nvidia/nemotron-3-nano-omni
    LMSTUDIO_API_KEY   default lm-studio

Set these in your shell, in `.envrc` (direnv), or in `.e2e-testing.env` if your
shell loads dotenv files automatically. The MCP server is launched by Claude
Code, so its env inherits from whatever launched Claude Code itself.
"""
# NOTE: do NOT add `from __future__ import annotations` here — FastMCP introspects
# tool signatures at registration time, and stringified annotations break its
# Context-arg detection on some `mcp` package versions.

import base64
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni"
DEFAULT_API_KEY = "lm-studio"
DEFAULT_MAX_TOKENS = 512
DEFAULT_TIMEOUT_S = 60

mcp = FastMCP("e2e-vision")


def _analyze(image_path: str, prompt: str) -> str:
    from openai import OpenAI

    p = Path(image_path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"image not found: {p}")
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")

    suffix = p.suffix.lower().lstrip(".") or "png"
    mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"

    base_url = os.environ.get("LMSTUDIO_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("LMSTUDIO_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("LMSTUDIO_API_KEY", DEFAULT_API_KEY)

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=DEFAULT_TIMEOUT_S)
    try:
        rsp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            max_tokens=DEFAULT_MAX_TOKENS,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"LM Studio call failed ({base_url}, model={model}): {type(e).__name__}: {e}"
        ) from e

    if not rsp.choices:
        raise RuntimeError("LM Studio returned no choices")
    text = (rsp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("LM Studio returned empty content")
    return text


@mcp.tool()
def analyze_image(path: str, prompt: str) -> str:
    """Ask the local LM Studio model about an image on disk. Returns the text answer.

    Args:
        path: Absolute or ~-relative path to a PNG/JPG file.
        prompt: What to ask the model (e.g. "Is the login form visible? Reply yes/no, then describe what's on screen in one sentence.").
    """
    return _analyze(path, prompt)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
