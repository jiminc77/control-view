from __future__ import annotations

from control_view.contracts.models import ControlViewResult


def serialize_control_view(result: ControlViewResult) -> dict:
    return result.model_dump(mode="json")

