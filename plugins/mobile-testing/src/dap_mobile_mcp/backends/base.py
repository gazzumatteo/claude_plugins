from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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

    async def shutdown(self) -> None:
        pass
