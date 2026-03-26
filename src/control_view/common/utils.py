from __future__ import annotations

import json
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

