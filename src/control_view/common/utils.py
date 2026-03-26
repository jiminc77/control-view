from __future__ import annotations

import json
import math
import zlib
from types import SimpleNamespace
from typing import Any


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def deep_get(value: Any, dotted_path: str, default: Any = None) -> Any:
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
        if current is default:
            return default
    return current


def to_namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: to_namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [to_namespace(item) for item in value]
    return value


def normalize_bool_literals(expression: str) -> str:
    return (
        expression.replace(" true", " True")
        .replace(" false", " False")
        .replace(" null", " None")
        .replace("==true", "==True")
        .replace("==false", "==False")
        .replace("!=true", "!=True")
        .replace("!=false", "!=False")
    )


def quaternion_to_yaw(quaternion: dict[str, Any] | None) -> float:
    if not quaternion:
        return 0.0
    x = float(quaternion.get("x", 0.0))
    y = float(quaternion.get("y", 0.0))
    z = float(quaternion.get("z", 0.0))
    w = float(quaternion.get("w", 1.0))
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def distance_3d(point_a: dict[str, Any] | None, point_b: dict[str, Any] | None) -> float | None:
    if not point_a or not point_b:
        return None
    return math.sqrt(
        (float(point_a.get("x", 0.0)) - float(point_b.get("x", 0.0))) ** 2
        + (float(point_a.get("y", 0.0)) - float(point_b.get("y", 0.0))) ** 2
        + (float(point_a.get("z", 0.0)) - float(point_b.get("z", 0.0))) ** 2
    )


def point_in_polygon(point: dict[str, Any], polygon: list[dict[str, Any]]) -> bool:
    x = float(point.get("x", 0.0))
    y = float(point.get("y", 0.0))
    inside = False
    if len(polygon) < 3:
        return False
    previous = polygon[-1]
    for current in polygon:
        x1 = float(previous.get("x", 0.0))
        y1 = float(previous.get("y", 0.0))
        x2 = float(current.get("x", 0.0))
        y2 = float(current.get("y", 0.0))
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            slope_x = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
            if x < slope_x:
                inside = not inside
        previous = current
    return inside


def stable_revision(value: Any) -> int:
    return zlib.crc32(stable_json_dumps(value).encode("ascii")) & 0xFFFFFFFF
