#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.0",
#     "openai>=1.50.0",
#     "python-dotenv>=1.0.0",
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

Configuration cascade (lower lines override higher lines, except process env which always wins):
    1. Process env (LMSTUDIO_BASE_URL, LMSTUDIO_MODEL, LMSTUDIO_API_KEY)
    2. <PWD>/.e2e-testing.env          — per-project config (also read by the local runner)
    3. ~/.config/claude-e2e-testing/config.env  — user-global

PWD is read from the env (the shell sets it) with a getcwd() fallback. The MCP
server's working directory is the plugin dir, but PWD reflects where Claude
Code was launched from — typically the user's project root, which is where the
dotenv files live.
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


def _load_env_cascade() -> None:
    """Load LMSTUDIO_* from .e2e-testing.env and the user-global config. Idempotent.

    `override=False` — values already in the process env (set by the shell that
    launched Claude Code) always win. Re-runs every call so the user can edit
    the dotenv file without restarting Claude Code.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    pwd_str = os.environ.get("PWD") or os.getcwd()
    pwd = Path(pwd_str).expanduser()
    home = Path.home()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))

    for candidate in (
        pwd / ".e2e-testing.env",
        xdg / "claude-e2e-testing" / "config.env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def _analyze(image_path: str, prompt: str) -> str:
    _load_env_cascade()  # picks up .e2e-testing.env from PWD on every call
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
