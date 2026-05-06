"""Vision-as-a-service helper: send an image + prompt to LM Studio, return text.

The MCP tools `analyze_screenshot` and `analyze_image` call into here. The whole
point is to let the orchestrating Claude session ask "what's on this screen?"
without paying ~1.8k vision tokens to ingest the PNG itself — LM Studio reads
the pixels, returns a short text answer, and Claude only sees the answer.

Configuration cascade (lower lines override higher lines, except process env which always wins):
    1. Process env (LMSTUDIO_BASE_URL, LMSTUDIO_MODEL, LMSTUDIO_API_KEY)
    2. <PWD>/.mobile-testing.env       — per-project mobile config
    3. <PWD>/.e2e-testing.env          — fallback for projects that share LM Studio config
    4. ~/.config/claude-mobile-testing/config.env  — user-global

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

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni"
DEFAULT_API_KEY = "lm-studio"
DEFAULT_MAX_TOKENS = 512
DEFAULT_TIMEOUT_S = 60


def _load_env_cascade() -> None:
    """Load LMSTUDIO_* from .{plugin}-testing.env files. Idempotent on every call.

    `override=False` — values already in the process env (set by the shell that
    launched Claude Code) always win. Re-runs every call so the user can edit
    a dotenv file without restarting Claude Code.
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
        pwd / ".mobile-testing.env",
        pwd / ".e2e-testing.env",
        xdg / "claude-mobile-testing" / "config.env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)


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
    _load_env_cascade()  # picks up .{plugin}-testing.env from PWD on every call
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

    base_url = os.environ.get("LMSTUDIO_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("LMSTUDIO_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("LMSTUDIO_API_KEY", DEFAULT_API_KEY)

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
