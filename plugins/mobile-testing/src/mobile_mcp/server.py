import atexit
import asyncio
import base64
import json
import os
import tempfile
import time

from mcp.server.fastmcp import FastMCP

from mobile_mcp.backends.base import BackendBase
from mobile_mcp.backends.native.router import NativeBackendRouter

mcp = FastMCP("mobile-testing")

_BACKEND: BackendBase | None = None


def _get_backend() -> BackendBase:
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    backend_name = os.environ.get("MOBILE_BACKEND", "native")
    if backend_name == "appium":
        from mobile_mcp.backends.appium.backend import AppiumBackend
        auto_start = os.environ.get("APPIUM_AUTO_START", "1") == "1"
        appium_url = os.environ.get("APPIUM_URL", "http://localhost:4723")
        _BACKEND = AppiumBackend(appium_url=appium_url, auto_start=auto_start)
    else:
        _BACKEND = NativeBackendRouter()

    atexit.register(_shutdown_backend_atexit)
    return _BACKEND


def _shutdown_backend_atexit() -> None:
    global _BACKEND
    if _BACKEND is None:
        return
    try:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return
        except RuntimeError:
            loop = asyncio.new_event_loop()
        loop.run_until_complete(_BACKEND.shutdown())
    except Exception:
        pass
    _BACKEND = None


@mcp.tool()
async def list_devices() -> str:
    """List all connected Android and iOS devices."""
    backend = _get_backend()
    devices = await backend.list_devices()
    return json.dumps([d.to_dict() for d in devices], indent=2)


@mcp.tool()
async def get_device(device_id: str) -> str:
    """Get details about a specific device.

    Args:
        device_id: Device ID to inspect
    """
    backend = _get_backend()
    device = await backend.get_device(device_id)
    return json.dumps(device.to_dict(), indent=2)


@mcp.tool()
async def start_bridge(device_id: str) -> str:
    """Start the automation bridge on a device. Required before interacting with the device.

    Args:
        device_id: Device ID
    """
    backend = _get_backend()
    result = await backend.start_bridge(device_id)
    return json.dumps(result)


@mcp.tool()
async def stop_bridge(device_id: str) -> str:
    """Stop the automation bridge on a device.

    Args:
        device_id: Device ID
    """
    backend = _get_backend()
    result = await backend.stop_bridge(device_id)
    return json.dumps(result)


@mcp.tool()
async def get_screenshot(device_id: str) -> list:
    """Get a low-quality screenshot from a device (for LLM analysis). Long edge ≤ 2000px.

    Args:
        device_id: Device ID
    """
    backend = _get_backend()
    img_data = await backend.get_screenshot(device_id, low_quality=True)
    return [{
        "type": "image",
        "data": base64.b64encode(img_data).decode("utf-8"),
        "mimeType": "image/png",
    }]


@mcp.tool()
async def save_screenshot(device_id: str, path: str = "", name: str | None = None) -> str:
    """Save a full-quality PNG screenshot to disk.

    Args:
        device_id: Device ID
        path: Directory to save screenshot to (supports ~/)
        name: Optional filename (without .png extension)
    """
    backend = _get_backend()
    fpath = await backend.save_screenshot(device_id, path=path, name=name)
    return f"Screenshot saved to: {fpath}"


@mcp.tool()
async def debug_app(device_id: str, bundle_id: str, log_path: str = "") -> str:
    """Launch an app in debug mode and write logs to a file.

    Args:
        device_id: Device ID
        bundle_id: Bundle ID of the app to debug
        log_path: Directory for log file (supports ~/)
    """
    backend = _get_backend()
    if not log_path:
        log_path = tempfile.gettempdir()
    log_path = os.path.expanduser(log_path)
    os.makedirs(log_path, exist_ok=True)
    ts = int(time.time())
    log_file = os.path.join(log_path, f"debug_{bundle_id}_{ts}.log")
    result = await backend.launch_app(device_id, bundle_id, fresh=True)
    return json.dumps({**result, "log_file": log_file})


@mcp.tool()
async def list_apps(device_id: str) -> str:
    """List installed apps on the device.

    Args:
        device_id: Device ID
    """
    backend = _get_backend()
    apps = await backend.list_apps(device_id)
    return json.dumps(apps, indent=2)


@mcp.tool()
async def install_app(device_id: str, path: str) -> str:
    """Install an app on the device from a local file path (.apk for Android, .ipa for iOS).

    Args:
        device_id: Device ID
        path: Local file path to the app (.apk or .ipa)
    """
    backend = _get_backend()
    result = await backend.install_app(device_id, path)
    return json.dumps(result)


@mcp.tool()
async def uninstall_app(device_id: str, bundle_id: str) -> str:
    """Uninstall an app from the device.

    Args:
        device_id: Device ID
        bundle_id: App bundle ID (iOS) or package name (Android)
    """
    backend = _get_backend()
    result = await backend.uninstall_app(device_id, bundle_id)
    return json.dumps(result)


@mcp.tool()
async def analyze_screenshot(device_id: str, prompt: str, save_path: str = "") -> str:
    """Capture a screenshot of the device and ask the local LM Studio model about it.

    Returns the model's text answer (~30-200 tokens). Use this instead of
    save_screenshot + Read(png) to validate UI state without paying Anthropic
    vision tokens — typical cost: ~1800 tok per Read(png) → ~50 tok per
    analyze_screenshot.

    Requires LMSTUDIO_BASE_URL / LMSTUDIO_MODEL / LMSTUDIO_API_KEY in the
    environment (defaults http://127.0.0.1:1234/v1, nvidia/nemotron-3-nano-omni,
    lm-studio). Raises a tool error if the endpoint is unreachable.

    Args:
        device_id: Device ID
        prompt: What to ask the model (e.g. "Is the login button visible? If yes, give x,y center coords.")
        save_path: Optional — also save the captured PNG to this path for evidence
    """
    from mobile_mcp.vision import analyze_with_lmstudio
    backend = _get_backend()
    img = await backend.get_screenshot(device_id, low_quality=True)
    if save_path:
        path = os.path.expanduser(save_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(img)
    answer = await asyncio.to_thread(analyze_with_lmstudio, img, prompt)
    return answer


@mcp.tool()
async def analyze_image(path: str, prompt: str) -> str:
    """Ask the local LM Studio model about an image already on disk.

    Companion to `analyze_screenshot` for cases where the PNG/JPG was saved by
    something else (e.g. Playwright `browser_take_screenshot(filename=...)`,
    or a previous `save_screenshot`). Returns the model's text answer.

    Args:
        path: Absolute or ~-relative path to the image file (PNG/JPG)
        prompt: What to ask the model
    """
    from mobile_mcp.vision import analyze_with_lmstudio
    answer = await asyncio.to_thread(analyze_with_lmstudio, path, prompt)
    return answer


@mcp.tool()
async def execute_dsl(device_id: str, commands: str) -> str:
    """Execute device automation steps using a JSON DSL script.

    The DSL must have `"version": "0.2"` and a `"steps"` array of actions.
    Supported actions: open_app, kill_app, tap, double_tap, long_press, type,
    swipe, scroll, drag, press_key, hide_keyboard, navigate, delay, wait_for,
    screenshot, observe, assert_exists, assert_not_exists, assert_screen_changed,
    assert_count, set_location, toggle.

    The `screenshot` action saves the PNG to disk (honoring `MOBILE_SCREENSHOT_DIR`)
    and returns `{"path": ...}` — it does not embed base64 to keep responses small.

    Args:
        device_id: Device ID
        commands: DSL script as JSON string with version and steps
    """
    backend = _get_backend()
    try:
        dsl = json.loads(commands)
    except json.JSONDecodeError as e:
        return f"Invalid DSL JSON: {e}"
    steps = dsl.get("steps", [])
    result = await backend.execute_dsl(device_id, steps)
    return json.dumps(result, indent=2)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
