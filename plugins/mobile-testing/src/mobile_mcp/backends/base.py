import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


def resolve_screenshot_dir(path: str = "") -> str:
    """Pick the screenshot output dir: explicit `path` > `MOBILE_SCREENSHOT_DIR` > tempdir.

    Callers (e.g. a test runner driving a project under test) export
    `MOBILE_SCREENSHOT_DIR=$PWD/.mobile-test-screenshots` and add that path to
    `.gitignore` so artifacts stay out of version control.
    """
    if path:
        resolved = os.path.expanduser(path)
    else:
        env = os.environ.get("MOBILE_SCREENSHOT_DIR", "").strip()
        resolved = os.path.expanduser(env) if env else tempfile.gettempdir()
    os.makedirs(resolved, exist_ok=True)
    return resolved


ANDROID_KEY_MAP: dict[str, str] = {
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "enter": "KEYCODE_ENTER",
    "tab": "KEYCODE_TAB",
    "delete": "KEYCODE_DEL",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "power": "KEYCODE_POWER",
    "recent_apps": "KEYCODE_APP_SWITCH",
    "mute": "KEYCODE_VOLUME_MUTE",
    "media_play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "escape": "KEYCODE_ESCAPE",
    "hide_keyboard": "111",  # KEYCODE_ESCAPE numeric — closes the soft keyboard
}

# iOS Appium maps keys to mobile: pressButton names
IOS_APPIUM_BUTTON_MAP: dict[str, str] = {
    "home": "home",
    "volume_up": "volumeup",
    "volume_down": "volumedown",
}


@dataclass
class Device:
    device_id: str
    name: str
    platform: str
    os_version: str
    virtual: bool = True
    state: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "platform": self.platform,
            "os_version": self.os_version,
            "virtual": self.virtual,
            "state": self.state,
        }


class BackendBase(ABC):

    name: str = "base"

    @abstractmethod
    async def health(self) -> dict: ...

    @abstractmethod
    async def list_devices(self) -> list[Device]: ...

    @abstractmethod
    async def get_device(self, device_id: str) -> Device: ...

    @abstractmethod
    async def start_bridge(self, device_id: str) -> dict: ...

    @abstractmethod
    async def stop_bridge(self, device_id: str) -> dict: ...

    @abstractmethod
    async def get_screenshot(self, device_id: str, low_quality: bool = True) -> bytes: ...

    @abstractmethod
    async def save_screenshot(self, device_id: str, path: str = "", name: str | None = None) -> str: ...

    @abstractmethod
    async def install_app(self, device_id: str, app_path: str) -> dict: ...

    @abstractmethod
    async def uninstall_app(self, device_id: str, bundle_id: str) -> dict: ...

    @abstractmethod
    async def list_apps(self, device_id: str) -> list[dict]: ...

    @abstractmethod
    async def launch_app(self, device_id: str, bundle_id: str, fresh: bool = False) -> dict: ...

    @abstractmethod
    async def kill_app(self, device_id: str, bundle_id: str) -> dict: ...

    @abstractmethod
    async def tap(self, device_id: str, x: int, y: int) -> dict: ...

    @abstractmethod
    async def type_text(self, device_id: str, text: str) -> dict: ...

    @abstractmethod
    async def swipe(
        self,
        device_id: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 300,
    ) -> dict: ...

    @abstractmethod
    async def press_key(self, device_id: str, key: str) -> dict: ...

    @abstractmethod
    async def observe(self, device_id: str, include: list[str] | None = None) -> dict: ...

    @abstractmethod
    async def execute_dsl(self, device_id: str, steps: list[dict]) -> dict: ...

    async def hide_keyboard(self, device_id: str) -> dict:
        """Default: send the soft-keyboard-dismiss keycode. Backends may override."""
        return await self.press_key(device_id, "hide_keyboard")

    async def shutdown(self) -> None:
        pass
