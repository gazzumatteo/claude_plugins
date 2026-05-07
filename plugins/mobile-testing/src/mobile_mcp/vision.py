"""Vision-as-a-service helper: send an image + prompt to LM Studio, return text.

The MCP tools `analyze_screenshot` and `analyze_image` call into here. The whole
point is to let the orchestrating Claude session ask "what's on this screen?"
without paying ~1.8k vision tokens to ingest the PNG itself — LM Studio reads
the pixels, returns a short text answer, and Claude only sees the answer.

Configuration: a single `.testing.yml` in the project root (see mobile_mcp/config.py
for the schema). Re-loaded on every tool call, so editing the YAML takes effect
without restarting Claude Code. Process env LMSTUDIO_* vars override YAML at runtime.

PWD is read from the env (the shell sets it) with a getcwd() fallback. Note
that the MCP server's working directory is the plugin dir (because
.mcp.json uses `--directory ${CLAUDE_PLUGIN_ROOT}`), but PWD reflects where
Claude Code was launched from, which is typically the user's project root.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Union

from .config import (
    DEFAULT_LMSTUDIO_API_KEY,
    DEFAULT_LMSTUDIO_BASE_URL,
    DEFAULT_LMSTUDIO_MODEL,
    ConfigError,
    Settings,
)

DEFAULT_MAX_TOKENS = 512
DEFAULT_TIMEOUT_S = 60


def analyze_with_lmstudio(
    image: Union[bytes, str, Path],
    prompt: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Send `image` (raw bytes or path) and `prompt` to LM Studio. Return the text reply.

    Raises RuntimeError on endpoint failure / empty response so the MCP layer
    can surface a clear tool-error to Claude (no silent fallback to "I don't
    know" — that would mask config problems).
    """
    # Re-load .testing.yml on every call so the user can edit it without restarting Claude Code.
    try:
        settings = Settings.load()
    except ConfigError as e:
        raise RuntimeError(f"config error: {e}") from e
    settings.apply_lmstudio_env()
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(
            "openai package missing — run "
            "`uv sync --directory ${CLAUDE_PLUGIN_ROOT}` to install required deps."
        ) from e

    if isinstance(image, (str, Path)):
        data = Path(image).expanduser().read_bytes()
    else:
        data = image
    b64 = base64.b64encode(data).decode("ascii")

    # Read from env AFTER apply_lmstudio_env() so that process-env vars (from
    # mcp.json or the shell) beat yaml defaults — same pattern as mobile_local_runner.py.
    base_url = os.environ.get("LMSTUDIO_BASE_URL", DEFAULT_LMSTUDIO_BASE_URL)
    model = os.environ.get("LMSTUDIO_MODEL", DEFAULT_LMSTUDIO_MODEL)
    api_key = os.environ.get("LMSTUDIO_API_KEY", DEFAULT_LMSTUDIO_API_KEY)

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s)
    try:
        rsp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
            max_tokens=max_tokens,
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
