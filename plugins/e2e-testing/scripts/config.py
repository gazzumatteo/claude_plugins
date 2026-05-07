"""Unified `.testing.yml` config loader for the e2e-testing and mobile-testing plugins.

A single `.testing.yml` in the project root drives both plugins. The schema is a
superset that covers web (browser/base_url) and mobile (device/apps); each plugin
reads only the keys it needs. When the two plugins eventually merge, this module
moves with no edits — the file is identical in both packages today.

Schema (every section optional except `executor:`):

    executor: cloud | local

    web:
      browser:
        engine: chromium | firefox | webkit
        headed: true
      base_url: https://staging.example.com

    mobile:
      device:
        platform: ios | android
        udid: <udid>
        name: "iPhone 15"
      apps:
        main: ./build/app.ipa

    lmstudio:
      base_url: http://127.0.0.1:1234/v1
      model: nvidia/nemotron-3-nano-omni
      api_key: ${LMSTUDIO_API_KEY}     # ${VAR} pulls from process env

    run:
      auto_confirm_destructive: false
      pre:  ["docker compose up -d"]
      post: ["docker compose logs > .e2e-runs/last-logs.txt"]

    credentials:
      admin:    {email: admin@x.com,    password: ${ADMIN_PWD}}
      reseller: {email: reseller@x.com, password: hunter2}

`${VAR}` interpolation runs on every string scalar and pulls from the process env
(empty string if unset). Process env is NOT mutated by `Settings.load()` — call
`settings.apply_lmstudio_env()` to push `lmstudio.*` into `os.environ` for libraries
(OpenAI client) that read it from there.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAME = ".testing.yml"

DEFAULT_LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_LMSTUDIO_MODEL = "nvidia/nemotron-3-nano-omni"
DEFAULT_LMSTUDIO_API_KEY = "lm-studio"

_VAR_RX = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class ConfigError(Exception):
    pass


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        return _VAR_RX.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


@dataclass
class LMStudio:
    base_url: str = DEFAULT_LMSTUDIO_BASE_URL
    model: str = DEFAULT_LMSTUDIO_MODEL
    api_key: str = DEFAULT_LMSTUDIO_API_KEY
    # Output budget per LM-Studio call. 8000 leaves comfortable headroom for
    # reasoning models (Qwen3-thinking, DeepSeek-R1, ...) that spend most tokens
    # on hidden reasoning before emitting tool_calls / content. Non-reasoning
    # models stop earlier at finish_step — the extra headroom is harmless.
    # Note: this is the OUTPUT cap, separate from the model's context window.
    max_tokens: int = 8000


@dataclass
class WebBrowser:
    engine: str = "chromium"
    headed: bool = False


@dataclass
class Web:
    browser: WebBrowser = field(default_factory=WebBrowser)
    base_url: str | None = None


@dataclass
class MobileDevice:
    platform: str | None = None
    udid: str | None = None
    name: str | None = None


@dataclass
class Mobile:
    device: MobileDevice = field(default_factory=MobileDevice)
    apps: dict[str, str] = field(default_factory=dict)


@dataclass
class Run:
    auto_confirm_destructive: bool = False
    max_iterations: int = 12
    strict_verdict: bool = False
    pre: list[str] = field(default_factory=list)
    post: list[str] = field(default_factory=list)


@dataclass
class Settings:
    executor: str = "cloud"
    web: Web = field(default_factory=Web)
    mobile: Mobile = field(default_factory=Mobile)
    lmstudio: LMStudio = field(default_factory=LMStudio)
    run: Run = field(default_factory=Run)
    credentials: dict[str, dict[str, str]] = field(default_factory=dict)
    source_path: Path | None = None

    @classmethod
    def load(cls, project_root: Path | str | None = None, *, required: bool = False) -> "Settings":
        """Load `.testing.yml` from project_root (defaults to PWD then CWD).

        Returns a default Settings if no file is found and `required=False`. The
        caller can detect this via `settings.source_path is None`.
        """
        path = _resolve_config_path(Path(project_root).expanduser() if project_root else None)
        if path is None:
            if required:
                raise ConfigError(
                    f"{CONFIG_FILENAME} not found (looked in project_root, $PWD, and cwd)"
                )
            return cls()

        try:
            import yaml
        except ImportError as e:
            raise ConfigError(
                "pyyaml not installed — `uv sync` (mobile-testing) or check the script's "
                "PEP 723 header (e2e-testing)"
            ) from e

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"{path} is not valid YAML: {e}") from e

        if not isinstance(raw, dict):
            raise ConfigError(f"{path} top-level must be a mapping, got {type(raw).__name__}")

        return _from_dict(_interpolate(raw), path)

    def apply_lmstudio_env(self) -> None:
        """Push lmstudio.* into os.environ. Existing process-env values win (setdefault).

        OpenAI client reads LMSTUDIO_BASE_URL / LMSTUDIO_MODEL / LMSTUDIO_API_KEY from
        the env. After this call, any subprocess inherits them too.
        """
        os.environ.setdefault("LMSTUDIO_BASE_URL", self.lmstudio.base_url)
        os.environ.setdefault("LMSTUDIO_MODEL", self.lmstudio.model)
        os.environ.setdefault("LMSTUDIO_API_KEY", self.lmstudio.api_key)


def _resolve_config_path(project_root: Path | None) -> Path | None:
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root / CONFIG_FILENAME)
    pwd = os.environ.get("PWD")
    if pwd:
        candidates.append(Path(pwd).expanduser() / CONFIG_FILENAME)
    candidates.append(Path.cwd() / CONFIG_FILENAME)
    seen: set[Path] = set()
    for c in candidates:
        c = c.resolve() if c.exists() else c
        if c in seen:
            continue
        seen.add(c)
        if c.exists():
            return c
    return None


def _from_dict(d: dict, source: Path) -> Settings:
    web_d = d.get("web") or {}
    if not isinstance(web_d, dict):
        web_d = {}
    browser_d = web_d.get("browser") or {}
    if not isinstance(browser_d, dict):
        browser_d = {}

    mobile_d = d.get("mobile") or {}
    if not isinstance(mobile_d, dict):
        mobile_d = {}
    device_d = mobile_d.get("device") or {}
    if not isinstance(device_d, dict):
        device_d = {}

    lm_d = d.get("lmstudio") or {}
    if not isinstance(lm_d, dict):
        lm_d = {}

    run_d = d.get("run") or {}
    if not isinstance(run_d, dict):
        run_d = {}

    creds = d.get("credentials") or {}
    if not isinstance(creds, dict):
        creds = {}

    return Settings(
        executor=str(d.get("executor", "cloud")).lower(),
        web=Web(
            browser=WebBrowser(
                engine=str(browser_d.get("engine", "chromium")),
                headed=bool(browser_d.get("headed", False)),
            ),
            base_url=web_d.get("base_url"),
        ),
        mobile=Mobile(
            device=MobileDevice(
                platform=device_d.get("platform"),
                udid=device_d.get("udid"),
                name=device_d.get("name"),
            ),
            apps={k: str(v) for k, v in (mobile_d.get("apps") or {}).items()},
        ),
        lmstudio=LMStudio(
            base_url=lm_d.get("base_url") or DEFAULT_LMSTUDIO_BASE_URL,
            model=lm_d.get("model") or DEFAULT_LMSTUDIO_MODEL,
            api_key=lm_d.get("api_key") or DEFAULT_LMSTUDIO_API_KEY,
            max_tokens=int(lm_d.get("max_tokens", 8000)),
        ),
        run=Run(
            auto_confirm_destructive=bool(run_d.get("auto_confirm_destructive", False)),
            max_iterations=int(run_d.get("max_iterations", 12)),
            strict_verdict=bool(run_d.get("strict_verdict", False)),
            pre=[str(s) for s in (run_d.get("pre") or [])],
            post=[str(s) for s in (run_d.get("post") or [])],
        ),
        credentials={
            role: {k: str(v) for k, v in (data or {}).items()}
            for role, data in creds.items()
            if isinstance(data, dict)
        },
        source_path=source,
    )
