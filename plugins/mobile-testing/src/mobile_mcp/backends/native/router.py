"""Native router — dispatches each call to iOS or Android sub-backend by device platform."""

import base64
from typing import Any

from mobile_mcp.backends.base import BackendBase, Device
from mobile_mcp.backends.native.android import NativeAndroidBackend
from mobile_mcp.backends.native.ios import NativeIOSBackend


class NativeBackendRouter(BackendBase):
    name = "native"

    def __init__(self) -> None:
        self._ios = NativeIOSBackend()
        self._android = NativeAndroidBackend()
        self._platform_cache: dict[str, str] = {}

    async def _platform_of(self, device_id: str) -> str:
        cached = self._platform_cache.get(device_id)
        if cached:
            return cached
        for d in await self.list_devices():
            self._platform_cache[d.device_id] = d.platform
        platform = self._platform_cache.get(device_id)
        if platform is None:
            raise ValueError(f"Device {device_id} not found on iOS or Android backends")
        return platform

    async def _backend_for(self, device_id: str) -> BackendBase:
        platform = await self._platform_of(device_id)
        return self._ios if platform == "ios" else self._android

    async def health(self) -> dict:
        return {
            "status": "ok",
            "backend": "native",
            "ios": await self._ios.health(),
            "android": await self._android.health(),
        }

    async def list_devices(self) -> list[Device]:
        devices: list[Device] = []
        try:
            devices.extend(await self._ios.list_devices())
        except Exception:
            pass
        try:
            devices.extend(await self._android.list_devices())
        except Exception:
            pass
        for d in devices:
            self._platform_cache[d.device_id] = d.platform
        return devices

    async def get_device(self, device_id: str) -> Device:
        backend = await self._backend_for(device_id)
        return await backend.get_device(device_id)

    async def start_bridge(self, device_id: str) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.start_bridge(device_id)

    async def stop_bridge(self, device_id: str) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.stop_bridge(device_id)

    async def get_screenshot(self, device_id: str, low_quality: bool = True) -> bytes:
        backend = await self._backend_for(device_id)
        return await backend.get_screenshot(device_id, low_quality=low_quality)

    async def save_screenshot(self, device_id: str, path: str = "", name: str | None = None) -> str:
        backend = await self._backend_for(device_id)
        return await backend.save_screenshot(device_id, path=path, name=name)

    async def install_app(self, device_id: str, app_path: str) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.install_app(device_id, app_path)

    async def uninstall_app(self, device_id: str, bundle_id: str) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.uninstall_app(device_id, bundle_id)

    async def list_apps(self, device_id: str) -> list[dict]:
        backend = await self._backend_for(device_id)
        return await backend.list_apps(device_id)

    async def launch_app(self, device_id: str, bundle_id: str, fresh: bool = False) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.launch_app(device_id, bundle_id, fresh=fresh)

    async def kill_app(self, device_id: str, bundle_id: str) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.kill_app(device_id, bundle_id)

    async def tap(self, device_id: str, x: int, y: int) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.tap(device_id, x, y)

    async def type_text(self, device_id: str, text: str) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.type_text(device_id, text)

    async def swipe(
        self,
        device_id: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 300,
    ) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.swipe(device_id, start_x, start_y, end_x, end_y, duration_ms=duration_ms)

    async def press_key(self, device_id: str, key: str) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.press_key(device_id, key)

    async def observe(self, device_id: str, include: list[str] | None = None) -> dict:
        backend = await self._backend_for(device_id)
        return await backend.observe(device_id, include=include)

    async def execute_dsl(self, device_id: str, steps: list[dict]) -> dict:
        from mobile_mcp.dsl.parser import execute_dsl_steps
        return await execute_dsl_steps(self, device_id, steps)

    async def shutdown(self) -> None:
        await self._ios.shutdown()
        await self._android.shutdown()
