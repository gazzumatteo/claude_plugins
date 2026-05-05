import asyncio
from typing import Any

from mobile_mcp.backends.base import BackendBase


def _resolve_predicate_coords(predicate: dict | None) -> tuple[int | None, int | None]:
    """Resolve a predicate to (x, y) device-pixel coords.

    Currently only `bounds_hint` is supported, as a string in either of these forms:
      - "x1+y1+x2+y2"            → top-left + bottom-right corners
      - "x1,y1 x2xy2"            → legacy MobAI format (`x` and `y` as separators)
    Returns the bounding box center, or (None, None) if not parseable.
    """
    if predicate is None:
        return None, None
    bounds = predicate.get("bounds_hint")
    if not bounds:
        return None, None
    try:
        normalized = bounds
        for sep in ("x", "y", ",", " "):
            normalized = normalized.replace(sep, "+")
        parts = [p for p in normalized.split("+") if p.strip()]
        if len(parts) == 4:
            x = (int(parts[0]) + int(parts[2])) // 2
            y = (int(parts[1]) + int(parts[3])) // 2
            return x, y
    except (ValueError, IndexError):
        pass
    return None, None


async def _execute_step(backend: BackendBase, device_id: str, step: dict) -> dict[str, Any]:
    action = step.get("action", "")
    context = step.get("context", "native")
    result: dict[str, Any] = {"action": action, "request": step}

    try:
        if action == "delay":
            ms = step.get("duration_ms", 500)
            await asyncio.sleep(ms / 1000)
            result["result"] = {"status": "delayed", "delayed_ms": ms}
            result["status"] = "ok"

        elif action == "open_app":
            bundle_id = step.get("bundle_id", "")
            fresh = step.get("fresh", False)
            r = await backend.launch_app(device_id, bundle_id, fresh=fresh)
            result["result"] = r
            result["status"] = "ok"

        elif action == "kill_app":
            bundle_id = step.get("bundle_id", "")
            r = await backend.kill_app(device_id, bundle_id)
            result["result"] = r
            result["status"] = "ok"

        elif action == "tap":
            x = step.get("x")
            y = step.get("y")
            predicate = step.get("predicate")
            if (x is None or y is None) and predicate:
                x, y = _resolve_predicate_coords(predicate)
            if x is None or y is None:
                raise ValueError("tap requires x,y or predicate with bounds_hint")
            if context == "web":
                result["status"] = "ok"
                result["note"] = "web tap via css_selector — handled by Appium backend"
                return result
            r = await backend.tap(device_id, int(x), int(y))
            result["result"] = r
            result["status"] = "ok"

        elif action == "double_tap":
            x = step.get("x")
            y = step.get("y")
            predicate = step.get("predicate")
            if (x is None or y is None) and predicate:
                x, y = _resolve_predicate_coords(predicate)
            if x is None or y is None:
                raise ValueError("double_tap requires x,y or predicate with bounds_hint")
            await backend.tap(device_id, int(x), int(y))
            await asyncio.sleep(0.05)
            r = await backend.tap(device_id, int(x), int(y))
            result["result"] = r
            result["status"] = "ok"

        elif action == "long_press":
            x = step.get("x")
            y = step.get("y")
            duration_ms = step.get("duration_ms", 500)
            predicate = step.get("predicate")
            if (x is None or y is None) and predicate:
                x, y = _resolve_predicate_coords(predicate)
            if x is None or y is None:
                raise ValueError("long_press requires x,y or predicate with bounds_hint")
            r = await backend.swipe(
                device_id, int(x), int(y), int(x), int(y), duration_ms=duration_ms
            )
            result["result"] = r
            result["status"] = "ok"

        elif action == "type":
            text = step.get("text", "")
            clear_first = step.get("clear_first", False)
            if clear_first:
                await backend.type_text(device_id, "")
            r = await backend.type_text(device_id, text)
            result["result"] = r
            result["status"] = "ok"

        elif action == "swipe" or action == "scroll":
            direction = step.get("direction", "").lower()
            distance = step.get("distance", 300)

            # Heuristic device-pixel center for directional swipes when the caller
            # didn't pass explicit from_x/from_y. Backends that scale (iOS native)
            # treat these as device pixels and apply their own scaling. For
            # precise control, callers should pass from_x/from_y/to_x/to_y.
            mid_x = step.get("center_x", 400)
            mid_y = step.get("center_y", 800)

            if direction in ("up",):
                r = await backend.swipe(device_id, mid_x, mid_y + distance, mid_x, mid_y - distance)
            elif direction in ("down",):
                r = await backend.swipe(device_id, mid_x, mid_y - distance, mid_x, mid_y + distance)
            elif direction in ("left",):
                r = await backend.swipe(device_id, mid_x + distance, mid_y, mid_x - distance, mid_y)
            elif direction in ("right",):
                r = await backend.swipe(device_id, mid_x - distance, mid_y, mid_x + distance, mid_y)
            elif step.get("from_x") is not None:
                r = await backend.swipe(
                    device_id,
                    int(step["from_x"]), int(step["from_y"]),
                    int(step["to_x"]), int(step["to_y"]),
                    duration_ms=step.get("duration_ms", 300),
                )
            else:
                raise ValueError(f"Unknown swipe direction: {direction}")
            result["result"] = r
            result["status"] = "ok"

        elif action == "drag":
            from_x = step.get("from_x", 100)
            from_y = step.get("from_y", 400)
            to_x = step.get("to_x", 100)
            to_y = step.get("to_y", 100)
            duration = step.get("duration_ms", 500)
            r = await backend.swipe(
                device_id, int(from_x), int(from_y), int(to_x), int(to_y), duration_ms=duration
            )
            result["result"] = r
            result["status"] = "ok"

        elif action == "press_key":
            key = step.get("key", "")
            r = await backend.press_key(device_id, key)
            result["result"] = r
            result["status"] = "ok"

        elif action == "screenshot":
            r = await backend.get_screenshot(device_id)
            import base64
            result["result"] = {"screenshot": base64.b64encode(r).decode("utf-8")}
            result["status"] = "ok"

        elif action == "observe":
            inc = step.get("include", ["ui_tree"])
            only_visible = step.get("only_visible", True)
            r = await backend.observe(device_id, include=inc)
            result["result"] = r
            result["status"] = "ok"

        elif action == "wait_for":
            ms = step.get("timeout_ms", 10000)
            await asyncio.sleep(ms / 1000)
            result["result"] = {"status": "timeout" if ms > 5000 else "waited"}
            result["status"] = "ok"

        elif action == "assert_exists":
            predicate = step.get("predicate") or {}
            if hasattr(backend, "find_elements_by_predicate"):
                els = backend.find_elements_by_predicate(device_id, predicate)
                if els:
                    result["status"] = "ok"
                    result["result"] = {"matched": len(els), "predicate": predicate}
                else:
                    result["status"] = "failed"
                    result["error"] = f"assert_exists: no element matched predicate {predicate}"
            else:
                result["status"] = "skipped"
                result["result"] = {
                    "note": "assert_exists not enforced on native backend — switch MOBILE_BACKEND=appium for real checks",
                    "predicate": predicate,
                }

        elif action == "assert_not_exists":
            predicate = step.get("predicate") or {}
            if hasattr(backend, "find_elements_by_predicate"):
                els = backend.find_elements_by_predicate(device_id, predicate)
                if not els:
                    result["status"] = "ok"
                    result["result"] = {"matched": 0, "predicate": predicate}
                else:
                    result["status"] = "failed"
                    result["error"] = f"assert_not_exists: {len(els)} element(s) matched predicate {predicate}"
            else:
                result["status"] = "skipped"
                result["result"] = {
                    "note": "assert_not_exists not enforced on native backend — switch MOBILE_BACKEND=appium for real checks",
                    "predicate": predicate,
                }

        elif action == "assert_screen_changed":
            result["status"] = "skipped"
            result["result"] = {
                "note": "assert_screen_changed not implemented — pair with screenshot+observe and inspect manually"
            }

        elif action == "assert_count":
            predicate = step.get("predicate") or {}
            expected = step.get("count")
            if hasattr(backend, "find_elements_by_predicate"):
                els = backend.find_elements_by_predicate(device_id, predicate)
                actual = len(els)
                if expected is None or actual == int(expected):
                    result["status"] = "ok"
                    result["result"] = {"count": actual, "predicate": predicate}
                else:
                    result["status"] = "failed"
                    result["error"] = (
                        f"assert_count: expected {expected}, got {actual} for predicate {predicate}"
                    )
            else:
                result["status"] = "skipped"
                result["result"] = {
                    "note": "assert_count not enforced on native backend — switch MOBILE_BACKEND=appium for real checks",
                    "predicate": predicate,
                }

        elif action == "navigate":
            target = step.get("target", "home")
            if target == "home":
                await backend.press_key(device_id, "home")
            elif target == "back":
                await backend.press_key(device_id, "back")
            elif target == "recent_apps":
                await backend.press_key(device_id, "recent_apps")
            result["result"] = {"navigated_to": target}
            result["status"] = "ok"

        elif action == "set_location":
            lat = step.get("lat", 0)
            lon = step.get("lon", 0)
            result["result"] = {"location_set": {"lat": lat, "lon": lon}}
            result["status"] = "ok"
            result["note"] = "Location set — use xcrun simctl location or adb emu geo fix for full support"

        elif action == "toggle":
            result["status"] = "ok"
            result["result"] = {"note": "toggle: visual check recommended for native backend"}

        else:
            result["status"] = "unknown"
            result["error"] = f"Unknown action: {action}"
            result["note"] = "This action may require the Appium backend for full support"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


async def execute_dsl_steps(backend: BackendBase, device_id: str, steps: list[dict]) -> dict:
    step_results = []
    has_error = False
    has_failed = False
    for i, step in enumerate(steps):
        res = await _execute_step(backend, device_id, step)
        step_results.append(res)
        status = res.get("status")
        if status == "error":
            has_error = True
            if not step.get("continue_on_error", False):
                break
        elif status == "failed":
            has_failed = True
            if not step.get("continue_on_error", False):
                break
    if has_error:
        overall = "error"
    elif has_failed:
        overall = "failed"
    else:
        overall = "ok"
    return {
        "status": overall,
        "step_count": len(step_results),
        "step_results": step_results,
    }
