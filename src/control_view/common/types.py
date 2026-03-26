from __future__ import annotations

from enum import StrEnum
from typing import Any

JSONDict = dict[str, Any]


class FieldClass(StrEnum):
    KINEMATIC = "kinematic"
    EVENT_DISCRETE = "event_discrete"
    VERSIONED_ARTIFACT = "versioned_artifact"
    DERIVED_QUALITY = "derived_quality"


class SlotOwner(StrEnum):
    BACKEND = "backend"
    SIDECAR = "sidecar"


class ValidState(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"
    DISAGREED = "DISAGREED"
    UNCONFIRMED = "UNCONFIRMED"


class Verdict(StrEnum):
    ACT = "ACT"
    REFRESH = "REFRESH"
    SAFE_HOLD = "SAFE_HOLD"
    REFUSE = "REFUSE"


class ActionState(StrEnum):
    REQUESTED = "REQUESTED"
    ACKED_WEAK = "ACKED_WEAK"
    ACKED_STRONG = "ACKED_STRONG"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    ABORTED = "ABORTED"


class ObligationStatus(StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class EventType(StrEnum):
    SENSOR_OBS = "SENSOR_OBS"
    BACKEND_REQUEST = "BACKEND_REQUEST"
    BACKEND_ACK = "BACKEND_ACK"
    BACKEND_CONFIRM = "BACKEND_CONFIRM"
    OPERATOR_OVERRIDE = "OPERATOR_OVERRIDE"
    CONFIG_REVISION = "CONFIG_REVISION"
    INVALIDATOR = "INVALIDATOR"
    TIMER_TICK = "TIMER_TICK"
    DEBUG_PROBE = "DEBUG_PROBE"


def is_mapping(value: Any) -> bool:
    return isinstance(value, dict)
