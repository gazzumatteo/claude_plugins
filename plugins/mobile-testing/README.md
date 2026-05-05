# mobile-testing

Direct iOS Simulator and Android Emulator control via MCP — no MobAI desktop app, **zero rate limits**.

Drop-in replacement for the [MobAI MCP](https://github.com/MobAI-App/mobai-mcp): same 14 tool names, same DSL v0.2, same return shapes. Just plug and replace.

## Quick start

> [!IMPORTANT]  
> `claude plugin install` copies the plugin files and auto-registers the `mobile-testing` MCP server (via the bundled `.mcp.json`). You still need `/setup-mobile` afterwards to install Python dependencies (`uv sync`) — without them the server starts and immediately crashes. Restart Claude Code once after both steps.

```bash
# 1. Add marketplace
claude plugin marketplace add git@github.com:gazzumatteo/claude_plugins.git

# 2. Install plugin (copies files only — tools NOT available yet)
claude plugin install mobile-testing@gazzumatteo-claude-plugins

# 3. Bootstrap (runs uv sync, registers MCP in ~/.claude/mcp.json, optionally installs Appium)
/setup-mobile

# 4. Restart Claude Code → tools appear (list_devices, execute_dsl, ...)
list_devices
start_bridge  # on a device ID
execute_dsl   # with DSL JSON
```

### What `/setup-mobile` does

| Step | What | Mandatory? |
|---|---|---|
| `uv sync` | Installs `mcp`, `httpx`, `pyobjc-framework-Quartz` (~30 packages) | **Yes** — without this the MCP server crashes on start |
| Install Appium | `npm install -g appium` + XCUITest/UIAutomator2 drivers | No — only needed for Appium backend |
| Create Android AVD | `avdmanager create` with Android 35 + Pixel 9 | No — only for Android emulator testing |

> MCP registration is handled by the bundled `.mcp.json` at install time. No manual edit of `~/.claude.json` is needed.

## Architecture

```
Claude Code
  ↓ stdio (MCP)
mobile-testing (Python)
  ├── NativeBackend  (default) — xcrun simctl + adb + Quartz/osascript
  └── AppiumBackend (opt-in)   — Appium 3.x + XCUITest/UIAutomator2
       ↓ auto-starts Appium on first use
```

## Backend comparison

| Feature | Native | Appium |
|---|---|---|
| iOS touch | Quartz CGEvent (needs Simulator window focused) | XCUITest (robust) |
| Android touch | adb input tap/swipe | UIAutomator2 |
| UI tree (observe) | Android: uiautomator dump. iOS: limited | Full XML page source |
| Setup | Zero extra deps | npm install -g appium + drivers |
| Speed | Instant | 5-15s session creation (WDA compilation on iOS) |
| WebViews | Not supported | Full support |
| Rate limits | None | None |

## Configuration

### MCP config (bundled — auto-registered)

The plugin ships a `.mcp.json` in its root that Claude Code reads at install time. You don't need to edit any user-level config.

```json
{
  "mcpServers": {
    "mobile-testing": {
      "command": "uv",
      "args": ["run", "--directory", "${CLAUDE_PLUGIN_ROOT}", "python", "-m", "mobile_mcp.server"],
      "env": {
        "MOBILE_BACKEND": "native",
        "APPIUM_URL": "http://localhost:4723",
        "APPIUM_AUTO_START": "1"
      }
    }
  }
}
```

> `${CLAUDE_PLUGIN_ROOT}` is resolved by Claude Code to the plugin install directory.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `MOBILE_BACKEND` | `native` | `native` or `appium` |
| `APPIUM_URL` | `http://localhost:4723` | Appium server URL |
| `APPIUM_AUTO_START` | `1` | Auto-start Appium when backend is appium |

## DSL reference

The `execute_dsl` tool accepts a JSON DSL with `version` and `steps`:

```json
{
  "version": "0.2",
  "steps": [
    {"action": "open_app", "bundle_id": "com.apple.Preferences", "fresh": true},
    {"action": "delay", "duration_ms": 2000},
    {"action": "tap", "x": 200, "y": 400},
    {"action": "type", "text": "hello"},
    {"action": "swipe", "direction": "up"},
    {"action": "screenshot"},
    {"action": "observe", "include": ["ui_tree", "screenshot"]},
    {"action": "press_key", "key": "home"},
    {"action": "navigate", "target": "home"},
    {"action": "assert_exists", "predicate": {"text": "General"}}
  ]
}
```

### All step actions

| Action | Parameters |
|---|---|
| `open_app` | `bundle_id`, `fresh` (bool) |
| `kill_app` | `bundle_id` |
| `tap` | `x`, `y` |
| `double_tap` | `x`, `y` |
| `long_press` | `x`, `y`, `duration_ms` |
| `type` | `text`, `clear_first` (bool) |
| `swipe` | `direction` (up/down/left/right) or `from_x`/`from_y`/`to_x`/`to_y` |
| `scroll` | `direction`, `distance` |
| `drag` | `from_x`, `from_y`, `to_x`, `to_y`, `duration_ms` |
| `press_key` | `key` (home, back, enter, tab, delete, volume_up, volume_down, ...) |
| `navigate` | `target` (home, back, recent_apps) |
| `delay` | `duration_ms` |
| `wait_for` | `timeout_ms` |
| `screenshot` | (none) |
| `observe` | `include` (list: ui_tree, screenshot, ocr, installed_apps, ...) |
| `assert_exists` | `predicate` |
| `assert_not_exists` | `predicate` |
| `assert_screen_changed` | `threshold_percent` |
| `assert_count` | `predicate`, `count` |
| `set_location` | `lat`, `lon` |
| `toggle` | `predicate`, `state` |

## Commands

| Command | Purpose |
|---|---|
| `/setup-mobile` | Bootstrap: install Appium, Python deps, create AVD, register MCP config |
| `/mobile-doctor` | Health check: verify all prerequisites and connections |

## Requirements

### Pre-install (must exist before `/setup-mobile`)

| Requirement | Check | Needed for |
|---|---|---|
| **Python 3.11+** | `python3 --version` | MCP server |
| **uv** | `which uv` or `curl -LsSf https://astral.sh/uv/install.sh \| sh` | Dependency install |
| **Xcode** | `xcrun simctl list devices` | iOS Simulator |
| **Android SDK** | `adb devices` | Android Emulator (optional) |
| **Node.js** | `node --version` | Appium backend (optional) |

### Post-install (handled by `/setup-mobile`)

| What | Command | Time |
|---|---|---|
| Python deps | `uv sync` (30 packages) | ~10s |
| MCP config | Registered in `~/.claude/mcp.json` | instant |
| Appium (opt) | `npm install -g appium` + drivers | ~2 min |
| Android AVD (opt) | `avdmanager create avd` | ~1 min |

> Run `/mobile-doctor` anytime to verify everything is correctly set up.

## License

MIT — see [LICENSE](../../LICENSE)

## Links

- Maintainer: [Matteo Gazzurelli](https://github.com/gazzumatteo) · matteo@gazzurelli.com
- Repository: [gazzumatteo/claude_plugins](https://github.com/gazzumatteo/claude_plugins)
