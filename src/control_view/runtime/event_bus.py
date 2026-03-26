from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel

from control_view.common.time import monotonic_ns, wall_time_iso
from control_view.common.types import EventType, JSONDict
from control_view.storage.ledger import LedgerRepository


class NormalizedEvent(BaseModel):
    event_id: str
    event_type: EventType
    source: str
    received_mono_ns: int
    received_wall_time: str
    source_header_stamp: str | None = None
    payload_json: JSONDict


class EventBus:
    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    def publish(
        self,
        event_type: EventType,
        source: str,
        payload_json: JSONDict,
        *,
        source_header_stamp: str | None = None,
    ) -> NormalizedEvent:
        event = NormalizedEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            source=source,
            received_mono_ns=monotonic_ns(),
            received_wall_time=wall_time_iso(),
            source_header_stamp=source_header_stamp,
            payload_json=payload_json,
        )
        self._ledger.append(event)
        return event

