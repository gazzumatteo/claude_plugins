#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.0",
#     "openai>=1.50.0",
#     "pyyaml>=6.0",
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

Configuration: a single `.testing.yml` in the project root (see scripts/config.py
for the schema). Re-loaded on every tool call, so editing the YAML takes effect
without restarting Claude Code. Process env LMSTUDIO_* vars override YAML at runtime.

PWD is read from the env (the shell sets it) with a getcwd() fallback. The MCP
server's working directory is the plugin dir, but PWD reflects where Claude
Code was launched from — typically the user's project root, which is where the
.testing.yml file lives.
"""
# NOTE: do NOT add `from __future__ import annotations` here — FastMCP introspects
# tool signatures at registration time, and stringified annotations break its
# Context-arg detection on some `mcp` package versions.

import base64
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Sibling import (PEP 723 script — add scripts/ to sys.path before importing config).
sys.path.insert(0, str(Path(__file__).parent))
from config import ConfigError, Settings  # noqa: E402

DEFAULT_MAX_TOKENS = 8000  # generous: reasoning models (Qwen3-thinking, R1) need headroom
DEFAULT_TIMEOUT_S = 60

mcp = FastMCP("e2e-vision")


def _analyze(image_path: str, prompt: str) -> str:
    # Re-load .testing.yml on every call so the user can edit it without restarting Claude Code.
    try:
        settings = Settings.load()
    except ConfigError as e:
        raise RuntimeError(f"config error: {e}") from e
    settings.apply_lmstudio_env()
    from openai import OpenAI

    p = Path(image_path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"image not found: {p}")
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")

    suffix = p.suffix.lower().lstrip(".") or "png"
    mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"

    base_url = settings.lmstudio.base_url
    model = settings.lmstudio.model
    api_key = settings.lmstudio.api_key

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
