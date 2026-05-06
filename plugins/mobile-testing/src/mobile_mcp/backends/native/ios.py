import asyncio
import base64
import json
import os
import subprocess
import tempfile
import time
from typing import Any

from mobile_mcp.backends.base import BackendBase, Device


def _xcrun(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    cmd = ["xcrun"] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _xcrun_json(*args: str, timeout: int = 30) -> dict:
    code, stdout, stderr = _xcrun(*args, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"xcrun failed (exit {code}): {stderr}")
    return json.loads(stdout)


def _osascript(script: str, timeout: int = 10) -> str:
    proc = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(f"osascript failed: {proc.stderr}")
    return proc.stdout.strip()


def _get_simulator_window_bounds() -> tuple[int, int, int, int]:
    """Return (x, y, width, height) in screen points of the front Simulator window."""
    try:
        pos = _osascript(
            'tell application "System Events" to get position of window 1 of process "Simulator"'
        )
        size = _osascript(
            'tell application "System Events" to get size of window 1 of process "Simulator"'
        )
        x, y = [int(v.strip()) for v in pos.split(",")]
        w, h = [int(v.strip()) for v in size.split(",")]
        return (x, y, w, h)
    except Exception:
        return (0, 0, 800, 600)


def _quartz_click(x_abs: int, y_abs: int) -> bool:
    try:
        import Quartz
    except ImportError:
        return False
    event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, (x_abs, y_abs), 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.05)
    event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, (x_abs, y_abs), 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    return True


def _simulator_click(x_abs: int, y_abs: int) -> None:
    if _quartz_click(x_abs, y_abs):
        return
    _osascript(
        f'tell application "System Events" to click at {{{x_abs}, {y_abs}}}',
        timeout=5,
    )


def _quartz_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
    try:
        import Quartz
    except ImportError:
        return False
    steps = max(10, duration_ms // 16)
    step_delay = duration_ms / 1000 / steps
    dx = (x2 - x1) / steps
    dy = (y2 - y1) / steps
    event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, (x1, y1), 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    for i in range(1, steps + 1):
        cur_x = x1 + dx * i
        cur_y = y1 + dy * i
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDragged, (cur_x, cur_y), 0
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(step_delay)
    time.sleep(0.05)
    event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, (x2, y2), 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    return True


def _simulator_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    if _quartz_swipe(x1, y1, x2, y2, duration_ms):
        return
    # osascript fallback — coarse, no native drag, but keeps cross-mac compatibility
    _osascript(
        f'''
tell application "System Events"
    click at {{{x1}, {y1}}}
    delay 0.1
    click at {{{x2}, {y2}}}
end tell
''',
        timeout=duration_ms / 1000 + 5,
    )


def _simulator_key_press(key: str) -> None:
    _osascript(f'tell application "System Events" to keystroke "{key}"', timeout=5)


def _simulator_key_code(code: int) -> None:
    _osascript(f'tell application "System Events" to key code {code}', timeout=5)


class NativeIOSBackend(BackendBase):

    name = "native-ios"

    def __init__(self) -> None:
        self._booted_by_us: set[str] = set()
        self._device_pixel_size: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _parse_runtime(runtime: str) -> str:
        prefix = "com.apple.CoreSimulator.SimRuntime."
        if runtime.startswith(prefix):
            runtime = runtime[len(prefix):]
        runtime = runtime.replace("iOS-", "").replace("-", ".")
        return runtime if "." in runtime else f"{runtime}.0"

    def _device_pixel_dims(self, device_id: str) -> tuple[int, int] | None:
        if device_id in self._device_pixel_size:
            return self._device_pixel_size[device_id]
        try:
            code, out, _ = _xcrun(
                "simctl", "io", device_id, "enumerate", "--json", timeout=10
            )
            if code != 0:
                return None
            data = json.loads(out)
        except Exception:
            return None
        for screen in data.get("screens", []) or []:
            default = (screen.get("default") or {})
            w = int(default.get("width") or 0)
            h = int(default.get("height") or 0)
            if w > 0 and h > 0:
                self._device_pixel_size[device_id] = (w, h)
                return (w, h)
        return None

    def _sim_to_abs(self, device_id: str, sx: int, sy: int) -> tuple[int, int]:
        """Convert device-pixel coords (sx, sy) to absolute macOS-screen-point coords.

        The Simulator window scales the device screen; the DSL passes device pixels
        but Quartz expects screen points. Read both sizes and apply the scale.
        """
        win_x, win_y, win_w, win_h = _get_simulator_window_bounds()
        device_dims = self._device_pixel_dims(device_id)
        if device_dims is None:
            return (win_x + sx, win_y + sy)
        dev_w, dev_h = device_dims
        scale_x = win_w / dev_w if dev_w else 1.0
        scale_y = win_h / dev_h if dev_h else 1.0
        return (int(win_x + sx * scale_x), int(win_y + sy * scale_y))

    async def health(self) -> dict:
        try:
            code, _, _ = _xcrun("simctl", "list", "devices", "booted")
            return {"status": "ok" if code == 0 else "error", "backend": "native-ios"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_devices(self) -> list[Device]:
        data = _xcrun_json("simctl", "list", "devices", "available", "--json")
        devices: list[Device] = []
        for runtime, devs in data.get("devices", {}).items():
            r_lower = runtime.lower()
            if any(t in r_lower for t in ("tv", "watch", "vision", "xr")):
                continue
            os_ver = self._parse_runtime(runtime)
            for d in devs:
                state = d.get("state", "Shutdown")
                devices.append(
                    Device(
                        device_id=d["udid"],
                        name=d["name"],
                        platform="ios",
                        os_version=os_ver,
                        virtual=True,
                        state=state,
                    )
                )
        return devices

    async def get_device(self, device_id: str) -> Device:
        for d in await self.list_devices():
            if d.device_id == device_id:
                return d
        raise ValueError(f"Device {device_id} not found")

    async def start_bridge(self, device_id: str) -> dict:
        device = await self.get_device(device_id)
        already_booted = device.state == "Booted"
        if not already_booted:
            code, _, err = _xcrun("simctl", "boot", device_id, timeout=60)
            if code != 0:
                raise RuntimeError(f"Failed to boot simulator: {err}")
            self._booted_by_us.add(device_id)
            await asyncio.sleep(3)
            _xcrun("simctl", "bootstatus", device_id, timeout=120)
        return {
            "status": "booted",
            "device_id": device_id,
            "was_already_booted": already_booted,
        }

    async def stop_bridge(self, device_id: str) -> dict:
        if device_id not in self._booted_by_us:
            return {
                "status": "left_running",
                "device_id": device_id,
                "note": "Simulator was not booted by this backend; left running.",
            }
        code, _, err = _xcrun("simctl", "shutdown", device_id, timeout=30)
        self._booted_by_us.discard(device_id)
        if code != 0:
            raise RuntimeError(f"Failed to shutdown simulator: {err}")
        return {"status": "shutdown", "device_id": device_id, "shutdown_by_us": True}

    async def get_screenshot(self, device_id: str, low_quality: bool = True) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            _xcrun("simctl", "io", device_id, "screenshot", "--type", "png", path, timeout=15)
            with open(path, "rb") as f:
                data = f.read()
            if low_quality and len(data) > 500_000:
                import io
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(data))
                    scale = max(1, max(img.size) // 2000)
                    if scale > 1:
                        img = img.resize(
                            (img.width // scale, img.height // scale),
                            Image.LANCZOS,
                        )
                    buf = io.BytesIO()
                    img.save(buf, format="PNG", optimize=True)
                    data = buf.getvalue()
                except ImportError:
                    pass
            return data
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def save_screenshot(self, device_id: str, path: str = "", name: str | None = None) -> str:
        from mobile_mcp.backends.base import resolve_screenshot_dir
        out_dir = resolve_screenshot_dir(path)
        ts = int(time.time())
        fname = name if name else f"screenshot_{device_id[:8]}_{ts}"
        fpath = os.path.join(out_dir, f"{fname}.png")
        _xcrun("simctl", "io", device_id, "screenshot", "--type", "png", fpath, timeout=15)
        return fpath

    async def install_app(self, device_id: str, app_path: str) -> dict:
        code, out, err = _xcrun("simctl", "install", device_id, app_path, timeout=120)
        if code != 0:
            raise RuntimeError(f"Install failed: {err}")
        return {"status": "installed", "device_id": device_id}

    async def uninstall_app(self, device_id: str, bundle_id: str) -> dict:
        code, out, err = _xcrun("simctl", "uninstall", device_id, bundle_id, timeout=30)
        if code != 0:
            raise RuntimeError(f"Uninstall failed: {err}")
        return {"status": "uninstalled", "device_id": device_id, "bundle_id": bundle_id}

    async def list_apps(self, device_id: str) -> list[dict]:
        code, out, err = _xcrun("simctl", "listapps", device_id, timeout=15)
        if code != 0:
            raise RuntimeError(f"listapps failed: {err}")
        try:
            proc = subprocess.run(
                ["plutil", "-convert", "json", "-", "-o", "-"],
                input=out, capture_output=True, text=True, timeout=10,
            )
            data = json.loads(proc.stdout)
        except Exception:
            data = {}
        apps = []
        for bundle_id, info in data.items():
            if info.get("ApplicationType", "User") != "User":
                continue
            apps.append({
                "bundle_id": bundle_id,
                "name": info.get("CFBundleDisplayName", bundle_id),
                "version": info.get("CFBundleShortVersionString", ""),
                "path": info.get("Bundle", ""),
            })
        return apps

    async def launch_app(self, device_id: str, bundle_id: str, fresh: bool = False) -> dict:
        if fresh:
            try:
                _xcrun("simctl", "terminate", device_id, bundle_id, timeout=10)
            except Exception:
                pass
        code, out, err = _xcrun("simctl", "launch", device_id, bundle_id, timeout=30)
        if code != 0:
            raise RuntimeError(f"Launch failed: {err}")
        pid = out.strip()
        return {"status": "launched", "device_id": device_id, "bundle_id": bundle_id, "pid": pid}

    async def kill_app(self, device_id: str, bundle_id: str) -> dict:
        _xcrun("simctl", "terminate", device_id, bundle_id, timeout=10)
        return {"status": "terminated", "device_id": device_id, "bundle_id": bundle_id}

    async def tap(self, device_id: str, x: int, y: int) -> dict:
        abs_x, abs_y = self._sim_to_abs(device_id, x, y)
        _simulator_click(abs_x, abs_y)
        await asyncio.sleep(0.1)
        return {"status": "tapped", "x": x, "y": y}

    async def type_text(self, device_id: str, text: str) -> dict:
        _simulator_key_press(text)
        return {"status": "typed", "text": text}

    async def swipe(
        self,
        device_id: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 300,
    ) -> dict:
        abs_x1, abs_y1 = self._sim_to_abs(device_id, start_x, start_y)
        abs_x2, abs_y2 = self._sim_to_abs(device_id, end_x, end_y)
        _simulator_swipe(abs_x1, abs_y1, abs_x2, abs_y2, duration_ms)
        await asyncio.sleep(duration_ms / 1000 + 0.1)
        return {
            "status": "swiped",
            "from": {"x": start_x, "y": start_y},
            "to": {"x": end_x, "y": end_y},
        }

    async def press_key(self, device_id: str, key: str) -> dict:
        key_map = {
            "home": 55,
            "enter": 36,
            "return": 36,
            "tab": 48,
            "delete": 51,
            "escape": 53,
            "hide_keyboard": 53,  # ESC dismisses the iOS Simulator soft keyboard
            "volume_up": 73,
            "volume_down": 74,
        }
        if key in key_map:
            _simulator_key_code(key_map[key])
        else:
            _simulator_key_press(key)
        return {"status": "key_pressed", "key": key}

    async def hide_keyboard(self, device_id: str) -> dict:
        _simulator_key_code(53)  # ESC
        return {"status": "keyboard_hidden"}

    async def observe(self, device_id: str, include: list[str] | None = None) -> dict:
        if include is None:
            include = ["ui_tree"]
        result: dict[str, Any] = {"device_id": device_id}
        if "screenshot" in include:
            data = await self.get_screenshot(device_id, low_quality=True)
            result["screenshot"] = base64.b64encode(data).decode("utf-8")
        if "ui_tree" in include:
            result["ui_tree"] = "iOS native ui_tree not available without Appium backend"
        if "installed_apps" in include:
            result["installed_apps"] = await self.list_apps(device_id)
        return result

    async def execute_dsl(self, device_id: str, steps: list[dict]) -> dict:
        from mobile_mcp.dsl.parser import execute_dsl_steps
        return await execute_dsl_steps(self, device_id, steps)
