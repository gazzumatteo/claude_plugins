---
name: mobile-testing
description: Direct mobile device control via MCP tools — tap, swipe, type, screenshot, observe, install apps, and more on iOS Simulator and Android Emulator. Supports native backend (xcrun simctl + adb) and Appium backend. Zero rate limits. Drop-in replacement for MobAI MCP. Load when the user asks to interact with mobile devices (iOS Simulator, Android Emulator, tap, swipe, screenshot, install app, test on device).
---

# Mobile Testing MCP (dap-mobile-mcp)

You have access to **dap-mobile-mcp** MCP tools for controlling iOS Simulator and Android Emulator directly — no MobAI desktop app, no rate limits.

## Backends

Two backends available, selected via the `MOBILE_BACKEND` environment variable:

| Backend | Value | iOS Touch | Android Touch | UI Tree | Setup |
|---|---|---|---|---|---|
| **Native** (default) | `native` | Quartz CGEvent + osascript | adb input tap/swipe | Limited (Android: uiautomator dump) | Zero extra deps |
| **Appium** | `appium` | XCUITest | UIAutomator2 | Full XML page source | Node.js + Appium + drivers |

## Before starting

1. Use `list_devices` to see connected simulators/emulators
2. Use `start_bridge` on the device you want to control
3. Use `execute_dsl` for all device actions — it supports tap, type, swipe, observe, launch apps, assertions, and more

## Tool reference

### Device Management
- **`list_devices`** — List all available iOS simulators and Android devices/emulators
- **`get_device`** — Get details about a specific device (requires `device_id`)
- **`start_bridge`** — Boot simulator or connect to device (required before interaction)
- **`stop_bridge`** — Shutdown simulator or disconnect

### Screenshots
- **`get_screenshot`** — Low-quality screenshot for LLM visual analysis (long edge ≤ 2000px)
- **`save_screenshot`** — Full-quality PNG to disk. Parameters: `device_id`, optional `path` and `name`

### App Management
- **`install_app`** — Install .app/.ipa (iOS) or .apk (Android). Parameters: `device_id`, `path`
- **`uninstall_app`** — Remove app. Parameters: `device_id`, `bundle_id`
- **`list_apps`** — List installed third-party apps. Parameter: `device_id`
- **`debug_app`** — Launch app in debug mode. Parameters: `device_id`, `bundle_id`, optional `log_path`

### Automation (via `execute_dsl`)
Use `execute_dsl` with a JSON DSL script containing `"version": "0.2"` and a `"steps"` array.

**Step reference:**

```json
{"action": "open_app",     "bundle_id": "com.apple.Preferences", "fresh": true}
{"action": "kill_app",     "bundle_id": "com.apple.Preferences"}
{"action": "tap",          "x": 200, "y": 400}
{"action": "double_tap",   "x": 200, "y": 400}
{"action": "long_press",   "x": 200, "y": 400, "duration_ms": 500}
{"action": "type",         "text": "hello"}
{"action": "swipe",        "direction": "up"}
{"action": "swipe",        "from_x": 100, "from_y": 500, "to_x": 100, "to_y": 100}
{"action": "scroll",       "direction": "down"}
{"action": "drag",         "from_x": 100, "from_y": 500, "to_x": 100, "to_y": 100}
{"action": "press_key",    "key": "home"}
{"action": "navigate",     "target": "home"}
{"action": "delay",        "duration_ms": 1000}
{"action": "wait_for",     "timeout_ms": 5000}
{"action": "screenshot"}
{"action": "observe",      "include": ["ui_tree", "screenshot"]}
```

**Assertions**

Real verification (status `ok` / `failed`) only on the **Appium** backend — uses `find_element` against the live UI tree. On the **native** backend, the same step records `status: "skipped"` with a note explaining the limitation. Switch backends via `MOBILE_BACKEND=appium`.

```json
{"action": "assert_exists",        "predicate": {"text": "General"}}
{"action": "assert_not_exists",    "predicate": {"text": "Error"}}
{"action": "assert_screen_changed"}
{"action": "assert_count",         "predicate": {"accessibility_id": "row"}, "count": 5}
```

Supported predicate keys (Appium): `text`, `accessibility_id`, `resource_id` (Android), `class_name`, `xpath`. The `bounds_hint` key (`"x1+y1+x2+y2"` or `"x1,y1 x2xy2"`) is supported by `tap` / `double_tap` / `long_press` for coordinate-based fallback.

## iOS vs Android differences

| Feature | iOS | Android |
|---|---|---|
| `press_key("back")` | Not available — use swipe | Available |
| `press_key("recent_apps")` | Not available — use swipe | Available |
| `navigate("back")` | Not available | Available |
| `set_location` | xcrun simctl location | adb emu geo fix |
| Web automation | Only via Appium, physical devices only | Available on emulators |

## Pattern: open app, take screenshot

```json
{
  "version": "0.2",
  "steps": [
    {"action": "open_app", "bundle_id": "com.apple.Preferences", "fresh": true},
    {"action": "delay", "duration_ms": 2000},
    {"action": "screenshot"},
    {"action": "tap", "x": 200, "y": 450},
    {"action": "delay", "duration_ms": 1000},
    {"action": "observe", "include": ["screenshot"]}
  ]
}
```

## Switching backends

Set the environment variable before invoking the MCP:

```bash
# Native backend (default, zero deps)
MOBILE_BACKEND=native

# Appium backend (requires Appium server + drivers)
MOBILE_BACKEND=appium APPIUM_URL=http://localhost:4723
```

The Appium backend auto-starts Appium if `APPIUM_AUTO_START=1` (default).
