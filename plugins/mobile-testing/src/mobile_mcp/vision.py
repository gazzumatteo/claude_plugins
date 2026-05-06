"""Vision-as-a-service helper: send an image + prompt to LM Studio, return text.

The MCP tools `analyze_screenshot` and `analyze_image` call into here. The whole
point is to let the orchestrating Claude session ask "what's on this screen?"
without paying ~1.8k vision tokens to ingest the PNG itself — LM Studio reads
the pixels, returns a short text answer, and Claude only sees the answer.

Configuration is read from environment variables (the same trio used by the
local runner):
    LMSTUDIO_BASE_URL  default http://127.0.0.1:1234/v1
    LMSTUDIO_MODEL     default nvidia/nemotron-3-nano-omni
    LMSTUDIO_API_KEY   default lm-studio
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
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(
            "openai package missing — run "
            "`uv sync --extra local --directory ${CLAUDE_PLUGIN_ROOT}` to enable analyze_* tools."
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
