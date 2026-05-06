import asyncio
import base64
import os
import subprocess
import tempfile
import time
from typing import Any

from mobile_mcp.backends.base import BackendBase, Device


class AppiumBackend(BackendBase):

    name = "appium"

    def __init__(
        self,
        appium_url: str = "http://localhost:4723",
        auto_start: bool = True,
    ):
        self._appium_url = appium_url.rstrip("/")
        self._auto_start = auto_start
        self._sessions: dict[str, Any] = {}
        self._appium_process: subprocess.Popen | None = None

    async def _ensure_appium_running(self) -> None:
        if not self._auto_start:
            return
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self._appium_url}/status", timeout=5)
                if r.status_code == 200:
                    return
        except Exception:
            pass

        self._appium_process = subprocess.Popen(
            ["appium", "--log-level", "error", "--relaxed-security"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    r = await client.get(f"{self._appium_url}/status", timeout=2)
                    if r.status_code == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(1)
        raise RuntimeError("Appium server did not start within 30 seconds")

    def _get_driver(self, device_id: str):
        if device_id not in self._sessions:
            raise RuntimeError(f"No Appium session for device {device_id}. Run start_bridge first.")
        return self._sessions[device_id]

    async def health(self) -> dict:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self._appium_url}/status", timeout=5)
                return {"status": "ok", "backend": "appium", "data": r.json()}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_devices(self) -> list[Device]:
        devices: list[Device] = []
        try:
            from mobile_mcp.backends.native.ios import NativeIOSBackend
            ios_devs = await NativeIOSBackend().list_devices()
            devices.extend(ios_devs)
        except Exception:
            pass
        try:
            from mobile_mcp.backends.native.android import NativeAndroidBackend
            android_devs = await NativeAndroidBackend().list_devices()
            devices.extend(android_devs)
        except Exception:
            pass
        return devices

    async def get_device(self, device_id: str) -> Device:
        for d in await self.list_devices():
            if d.device_id == device_id:
                return d
        raise ValueError(f"Device {device_id} not found")

    async def start_bridge(self, device_id: str) -> dict:
        await self._ensure_appium_running()
        from appium import webdriver
        from appium.options.ios import XCUITestOptions
        from appium.options.android import UiAutomator2Options
        from selenium.common.exceptions import WebDriverException

        device = await self.get_device(device_id)
        if device.platform == "ios":
            opts = XCUITestOptions()
            opts.udid = device_id
            opts.automation_name = "XCUITest"
            opts.auto_accept_alerts = True
            opts.new_command_timeout = 600
            if device.os_version:
                opts.platform_version = device.os_version
        else:
            opts = UiAutomator2Options()
            opts.udid = device_id
            opts.automation_name = "UiAutomator2"
            opts.new_command_timeout = 600
            if device.os_version:
                opts.platform_version = device.os_version

        try:
            # webdriver.Remote is sync and can block 15s+ on first WDA install;
            # offload to a thread so MCP stdio keepalive isn't starved.
            driver = await asyncio.to_thread(
                webdriver.Remote, self._appium_url, options=opts
            )
        except WebDriverException as e:
            raise RuntimeError(
                f"Appium session failed for {device_id}: {e.msg}"
            ) from e
        self._sessions[device_id] = driver
        return {"status": "connected", "session_id": driver.session_id, "device_id": device_id}

    async def stop_bridge(self, device_id: str) -> dict:
        if device_id in self._sessions:
            try:
                self._sessions[device_id].quit()
            except Exception:
                pass
            del self._sessions[device_id]
        return {"status": "disconnected", "device_id": device_id}

    async def get_screenshot(self, device_id: str, low_quality: bool = True) -> bytes:
        driver = self._get_driver(device_id)
        return driver.get_screenshot_as_png()

    async def save_screenshot(self, device_id: str, path: str = "", name: str | None = None) -> str:
        from mobile_mcp.backends.base import resolve_screenshot_dir
        out_dir = resolve_screenshot_dir(path)
        ts = int(time.time())
        fname = name if name else f"screenshot_{device_id[:8]}_{ts}"
        fpath = os.path.join(out_dir, f"{fname}.png")
        driver = self._get_driver(device_id)
        driver.save_screenshot(fpath)
        return fpath

    async def install_app(self, device_id: str, app_path: str) -> dict:
        driver = self._get_driver(device_id)
        driver.install_app(app_path)
        return {"status": "installed", "device_id": device_id}

    async def uninstall_app(self, device_id: str, bundle_id: str) -> dict:
        driver = self._get_driver(device_id)
        driver.remove_app(bundle_id)
        return {"status": "uninstalled", "device_id": device_id, "bundle_id": bundle_id}

    async def list_apps(self, device_id: str) -> list[dict]:
        return []  # Appium doesn't have a clean list_apps API

    async def launch_app(self, device_id: str, bundle_id: str, fresh: bool = False) -> dict:
        driver = self._get_driver(device_id)
        if fresh:
            try:
                driver.terminate_app(bundle_id)
            except Exception:
                pass
        driver.activate_app(bundle_id)
        return {"status": "launched", "device_id": device_id, "bundle_id": bundle_id}

    async def kill_app(self, device_id: str, bundle_id: str) -> dict:
        driver = self._get_driver(device_id)
        driver.terminate_app(bundle_id)
        return {"status": "terminated", "device_id": device_id, "bundle_id": bundle_id}

    async def tap(self, device_id: str, x: int, y: int) -> dict:
        driver = self._get_driver(device_id)
        driver.tap([(x, y)])
        return {"status": "tapped", "x": x, "y": y}

    async def type_text(self, device_id: str, text: str) -> dict:
        driver = self._get_driver(device_id)
        try:
            el = driver.switch_to.active_element
            el.send_keys(text)
        except Exception:
            driver.execute_script("mobile: type", {"text": text})
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
        driver = self._get_driver(device_id)
        driver.swipe(start_x, start_y, end_x, end_y, duration_ms)
        return {
            "status": "swiped",
            "from": {"x": start_x, "y": start_y},
            "to": {"x": end_x, "y": end_y},
        }

    async def press_key(self, device_id: str, key: str) -> dict:
        from mobile_mcp.backends.base import ANDROID_KEY_MAP, IOS_APPIUM_BUTTON_MAP
        driver = self._get_driver(device_id)
        device = await self.get_device(device_id)
        normalized = key.lower()
        if device.platform == "android":
            keycode = ANDROID_KEY_MAP.get(normalized, normalized.upper())
            # Appium expects an int keycode for press_keycode; KEYCODE_* names → numeric
            android_numeric: dict[str, int] = {
                "KEYCODE_HOME": 3, "KEYCODE_BACK": 4, "KEYCODE_ENTER": 66,
                "KEYCODE_TAB": 61, "KEYCODE_DEL": 67, "KEYCODE_VOLUME_UP": 24,
                "KEYCODE_VOLUME_DOWN": 25, "KEYCODE_POWER": 26,
                "KEYCODE_APP_SWITCH": 187, "KEYCODE_VOLUME_MUTE": 164,
                "KEYCODE_MEDIA_PLAY_PAUSE": 85, "KEYCODE_ESCAPE": 111,
            }
            num = android_numeric.get(keycode) if isinstance(keycode, str) else None
            if num is None:
                try:
                    num = int(keycode)
                except (TypeError, ValueError):
                    raise ValueError(f"press_key: unknown Android key '{key}'") from None
            driver.press_keycode(num)
        else:  # iOS
            button = IOS_APPIUM_BUTTON_MAP.get(normalized)
            if button:
                driver.execute_script("mobile: pressButton", {"name": button})
            else:
                raise ValueError(f"press_key: '{key}' not supported on iOS Appium")
        return {"status": "key_pressed", "key": key}

    async def hide_keyboard(self, device_id: str) -> dict:
        driver = self._get_driver(device_id)
        try:
            driver.hide_keyboard()
        except Exception:
            pass  # iOS sometimes raises when keyboard already hidden
        return {"status": "keyboard_hidden"}

    async def observe(self, device_id: str, include: list[str] | None = None) -> dict:
        if include is None:
            include = ["ui_tree"]
        driver = self._get_driver(device_id)
        result: dict[str, Any] = {"device_id": device_id}
        if "screenshot" in include:
            result["screenshot"] = driver.get_screenshot_as_base64()
        if "ui_tree" in include:
            result["ui_tree"] = driver.page_source
        return result

    async def execute_dsl(self, device_id: str, steps: list[dict]) -> dict:
        from mobile_mcp.dsl.parser import execute_dsl_steps
        return await execute_dsl_steps(self, device_id, steps)

    def find_elements_by_predicate(self, device_id: str, predicate: dict) -> list:
        """Find elements via Appium that match the predicate.

        Supported predicate keys: text (visible text), accessibility_id,
        resource_id (Android), name (iOS), class_name, xpath.
        """
        from appium.webdriver.common.appiumby import AppiumBy

        driver = self._get_driver(device_id)
        if "xpath" in predicate:
            return driver.find_elements(AppiumBy.XPATH, predicate["xpath"])
        if "accessibility_id" in predicate:
            return driver.find_elements(AppiumBy.ACCESSIBILITY_ID, predicate["accessibility_id"])
        if "resource_id" in predicate:
            return driver.find_elements(AppiumBy.ID, predicate["resource_id"])
        if "text" in predicate:
            text = predicate["text"]
            try:
                return driver.find_elements(
                    AppiumBy.IOS_PREDICATE, f'label == "{text}" OR name == "{text}" OR value == "{text}"'
                )
            except Exception:
                pass
            return driver.find_elements(
                AppiumBy.XPATH, f'//*[@text="{text}" or @content-desc="{text}"]'
            )
        if "class_name" in predicate:
            return driver.find_elements(AppiumBy.CLASS_NAME, predicate["class_name"])
        return []

    async def shutdown(self) -> None:
        for device_id in list(self._sessions.keys()):
            await self.stop_bridge(device_id)
        if self._appium_process:
            self._appium_process.terminate()
            self._appium_process.wait(timeout=10)
            self._appium_process = None
