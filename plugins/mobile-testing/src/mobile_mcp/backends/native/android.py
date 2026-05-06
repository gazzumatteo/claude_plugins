import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

from mobile_mcp.backends.base import BackendBase, Device


def _adb(*args: str, timeout: int = 30, device_id: str | None = None) -> tuple[int, str, str]:
    cmd = ["adb"]
    if device_id:
        cmd.extend(["-s", device_id])
    cmd.extend(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _adb_bytes(*args: str, timeout: int = 30, device_id: str | None = None) -> tuple[int, bytes, bytes]:
    """adb invocation that preserves binary stdout (e.g. screencap PNG output)."""
    cmd = ["adb"]
    if device_id:
        cmd.extend(["-s", device_id])
    cmd.extend(args)
    proc = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


class NativeAndroidBackend(BackendBase):

    name = "native-android"

    def __init__(self) -> None:
        self._started_avds: set[str] = set()

    async def health(self) -> dict:
        code, out, _ = _adb("devices")
        return {
            "status": "ok" if code == 0 and "\tdevice" in out else "error",
            "backend": "native-android",
        }

    async def list_devices(self) -> list[Device]:
        code, out, _ = _adb("devices", "-l")
        devices: list[Device] = []
        if code != 0:
            return devices
        for line in out.strip().split("\n")[1:]:
            if not line.strip() or "List of devices" in line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = "booted" if parts[1] == "device" else parts[1]
            model = "Android Device"
            for p in parts[2:]:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1]
            virtual = "emulator" in serial or "emulator" in line.lower()
            try:
                code, ver, _ = _adb("shell", "getprop", "ro.build.version.release", device_id=serial, timeout=5)
                os_ver = ver.strip() if code == 0 else "unknown"
            except Exception:
                os_ver = "unknown"
            devices.append(
                Device(
                    device_id=serial,
                    name=model,
                    platform="android",
                    os_version=os_ver,
                    virtual=virtual,
                    state=state,
                )
            )
        return devices

    async def get_device(self, device_id: str) -> Device:
        for d in await self.list_devices():
            if d.device_id == device_id:
                return d
        raise ValueError(f"Device {device_id} not found")

    async def _list_avds(self) -> list[str]:
        if shutil.which("emulator") is None:
            return []
        try:
            proc = subprocess.run(
                ["emulator", "-list-avds"],
                capture_output=True, text=True, timeout=10,
            )
            return [a for a in proc.stdout.splitlines() if a.strip()]
        except Exception:
            return []

    async def _wait_for_serial(self, serial: str, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            code, out, _ = _adb("devices")
            if code == 0:
                for line in out.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == serial and parts[1] == "device":
                        return True
            await asyncio.sleep(1)
        return False

    async def start_bridge(self, device_id: str) -> dict:
        code, out, _ = _adb("devices")
        if code == 0:
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device_id and parts[1] == "device":
                    return {"status": "already_running", "device_id": device_id}

        avds = await self._list_avds()
        if device_id in avds:
            if shutil.which("emulator") is None:
                raise RuntimeError("`emulator` binary not on PATH — install Android SDK emulator")
            subprocess.Popen(
                ["emulator", "-avd", device_id, "-no-snapshot-load"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._started_avds.add(device_id)
            booted = await self._wait_for_serial(device_id, timeout=120.0)
            if not booted:
                booted_any = False
                code, out, _ = _adb("devices")
                serial = None
                if code == 0:
                    for line in out.splitlines()[1:]:
                        parts = line.split()
                        if len(parts) >= 2 and parts[0].startswith("emulator-") and parts[1] == "device":
                            serial = parts[0]
                            booted_any = True
                            break
                if not booted_any:
                    raise RuntimeError(f"Emulator '{device_id}' did not become ready within 120s")
                return {"status": "started", "device_id": serial, "avd": device_id}
            return {"status": "started", "device_id": device_id}

        raise RuntimeError(
            f"Device '{device_id}' is not connected and no AVD with that name was found. "
            f"Run `adb devices` or `emulator -list-avds` to inspect."
        )

    async def stop_bridge(self, device_id: str) -> dict:
        if device_id in self._started_avds:
            _adb("emu", "kill", device_id=device_id, timeout=10)
            self._started_avds.discard(device_id)
            return {"status": "shutdown", "device_id": device_id, "shutdown_by_us": True}
        return {
            "status": "disconnected",
            "device_id": device_id,
            "note": "Emulator was not started by this backend; left running.",
        }

    async def get_screenshot(self, device_id: str, low_quality: bool = True) -> bytes:
        code, data, err = _adb_bytes("exec-out", "screencap", "-p", device_id=device_id, timeout=15)
        if code != 0:
            raise RuntimeError(f"Screenshot failed: {err.decode('utf-8', errors='replace')}")
        if low_quality and len(data) > 500_000:
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(data))
                scale = max(1, max(img.size) // 2000)
                if scale > 1:
                    img = img.resize(
                        (img.width // scale, img.height // scale),
                        Image.LANCZOS,
                    )
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=60)
                data = buf.getvalue()
            except ImportError:
                pass
        return data

    async def save_screenshot(self, device_id: str, path: str = "", name: str | None = None) -> str:
        from mobile_mcp.backends.base import resolve_screenshot_dir
        out_dir = resolve_screenshot_dir(path)
        ts = int(time.time())
        fname = name if name else f"screenshot_{device_id[:8]}_{ts}"
        fpath = os.path.join(out_dir, f"{fname}.png")
        code, data, err = _adb_bytes("exec-out", "screencap", "-p", device_id=device_id, timeout=15)
        if code != 0:
            raise RuntimeError(f"Screenshot failed: {err.decode('utf-8', errors='replace')}")
        with open(fpath, "wb") as f:
            f.write(data)
        return fpath

    async def install_app(self, device_id: str, app_path: str) -> dict:
        code, out, err = _adb("install", "-r", app_path, device_id=device_id, timeout=120)
        if code != 0:
            raise RuntimeError(f"Install failed: {err}")
        return {"status": "installed", "device_id": device_id}

    async def uninstall_app(self, device_id: str, bundle_id: str) -> dict:
        code, out, err = _adb("uninstall", bundle_id, device_id=device_id, timeout=30)
        if code != 0:
            raise RuntimeError(f"Uninstall failed: {err}")
        return {"status": "uninstalled", "device_id": device_id, "bundle_id": bundle_id}

    async def list_apps(self, device_id: str) -> list[dict]:
        code, out, err = _adb("shell", "pm", "list", "packages", "-3", device_id=device_id, timeout=15)
        apps = []
        if code == 0:
            for line in out.strip().split("\n"):
                if line.startswith("package:"):
                    pkg = line.replace("package:", "").strip()
                    apps.append({"bundle_id": pkg, "name": pkg.split(".")[-1], "platform": "android"})
        return apps

    async def launch_app(self, device_id: str, bundle_id: str, fresh: bool = False) -> dict:
        if fresh:
            _adb("shell", "am", "force-stop", bundle_id, device_id=device_id, timeout=10)
        _adb(
            "shell", "monkey", "-p", bundle_id,
            "-c", "android.intent.category.LAUNCHER", "1",
            device_id=device_id, timeout=15,
        )
        return {"status": "launched", "device_id": device_id, "bundle_id": bundle_id}

    async def kill_app(self, device_id: str, bundle_id: str) -> dict:
        _adb("shell", "am", "force-stop", bundle_id, device_id=device_id, timeout=10)
        return {"status": "terminated", "device_id": device_id, "bundle_id": bundle_id}

    async def tap(self, device_id: str, x: int, y: int) -> dict:
        _adb("shell", "input", "tap", str(x), str(y), device_id=device_id, timeout=10)
        await asyncio.sleep(0.1)
        return {"status": "tapped", "x": x, "y": y}

    async def type_text(self, device_id: str, text: str) -> dict:
        escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
        _adb("shell", "input", "text", escaped, device_id=device_id, timeout=10)
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
        _adb(
            "shell", "input", "swipe",
            str(start_x), str(start_y), str(end_x), str(end_y),
            str(duration_ms),
            device_id=device_id, timeout=15,
        )
        await asyncio.sleep(duration_ms / 1000 + 0.1)
        return {
            "status": "swiped",
            "from": {"x": start_x, "y": start_y},
            "to": {"x": end_x, "y": end_y},
        }

    async def press_key(self, device_id: str, key: str) -> dict:
        from mobile_mcp.backends.base import ANDROID_KEY_MAP
        keycode = ANDROID_KEY_MAP.get(key.lower(), key.upper())
        _adb("shell", "input", "keyevent", keycode, device_id=device_id, timeout=10)
        return {"status": "key_pressed", "key": key}

    async def hide_keyboard(self, device_id: str) -> dict:
        # On Android the BACK key dismisses the IME without affecting nav stack
        # when no keyboard is open (no-op).
        _adb("shell", "input", "keyevent", "KEYCODE_BACK", device_id=device_id, timeout=10)
        return {"status": "keyboard_hidden"}

    async def observe(self, device_id: str, include: list[str] | None = None) -> dict:
        if include is None:
            include = ["ui_tree"]
        result: dict[str, Any] = {"device_id": device_id}
        if "screenshot" in include:
            data = await self.get_screenshot(device_id, low_quality=True)
            result["screenshot"] = base64.b64encode(data).decode("utf-8")
        if "ui_tree" in include:
            try:
                _adb("shell", "uiautomator", "dump", "/sdcard/ui_tree.xml", device_id=device_id, timeout=15)
                with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
                    tmp = f.name
                try:
                    _adb("pull", "/sdcard/ui_tree.xml", tmp, device_id=device_id, timeout=10)
                    with open(tmp) as f:
                        result["ui_tree"] = f.read()
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            except Exception as e:
                result["ui_tree"] = f"ui_tree error: {e}"
        if "installed_apps" in include:
            result["installed_apps"] = await self.list_apps(device_id)
        return result

    async def execute_dsl(self, device_id: str, steps: list[dict]) -> dict:
        from mobile_mcp.dsl.parser import execute_dsl_steps
        return await execute_dsl_steps(self, device_id, steps)
