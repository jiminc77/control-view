from __future__ import annotations

from typing import Any

from control_view.backend.base import BackendAdapter, BackendSlotValue
from control_view.common.time import monotonic_ns, wall_time_iso
from control_view.common.types import EventType, ValidState
from control_view.contracts.models import EvidenceEntry, FieldSpec
from control_view.runtime.event_bus import EventBus
from control_view.storage.snapshots import SnapshotRepository


class Materializer:
    def __init__(
        self,
        fields: dict[str, FieldSpec],
        backend: BackendAdapter,
        snapshots: SnapshotRepository,
        event_bus: EventBus,
    ) -> None:
        self._fields = fields
        self._backend = backend
        self._snapshots = snapshots
        self._event_bus = event_bus

    def refresh_slots(self, slot_ids: list[str]) -> dict[str, EvidenceEntry]:
        current = self._snapshots.get_many(slot_ids)
        backend_values = self._backend.get_current_snapshot(slot_ids)
        resolved: dict[str, EvidenceEntry] = {}

        backend_first = sorted(slot_ids, key=lambda slot_id: self._fields[slot_id].owner.value)
        for slot_id in backend_first:
            previous = current.get(slot_id)
            raw_value = backend_values.get(slot_id)
            if raw_value is None:
                raw_value = self._backend.refresh_slot(slot_id)
            if raw_value is None:
                raw_value = self._derive_slot(slot_id, {**current, **resolved})
            entry = self._build_entry(slot_id, raw_value, previous)
            resolved[slot_id] = entry
            self._snapshots.upsert(entry)
            self._event_bus.publish(
                EventType.SENSOR_OBS,
                source="materializer",
                source_header_stamp=entry.source_header_stamp,
                payload_json={
                    "slot_id": slot_id,
                    "revision": entry.revision,
                    "valid_state": entry.valid_state.value,
                },
            )
        return resolved

    def _derive_slot(
        self,
        slot_id: str,
        available: dict[str, EvidenceEntry],
    ) -> BackendSlotValue | None:
        field = self._fields[slot_id]
        dependencies = list(field.derivation.get("dependencies", [])) if field.derivation else []
        if slot_id == "home.ready":
            home = available.get("home.position")
            connected = available.get("vehicle.connected")
            ready = bool(
                home
                and home.valid_state == ValidState.VALID
                and connected
                and connected.value_json
            )
            return BackendSlotValue(
                value={"ready": ready},
                authority_source="sidecar",
            )
        if slot_id == "tf.local_body":
            pose = available.get("pose.local")
            if not pose or not pose.value_json:
                return None
            return BackendSlotValue(
                value={
                    "frame_id": pose.value_json.get("frame_id"),
                    "child_frame_id": pose.value_json.get("child_frame_id"),
                },
                authority_source="sidecar",
                frame_id=pose.value_json.get("frame_id"),
            )
        if dependencies and all(dependency in available for dependency in dependencies):
            return None
        return None

    def _build_entry(
        self,
        slot_id: str,
        raw_value: BackendSlotValue | None,
        previous: EvidenceEntry | None,
    ) -> EvidenceEntry:
        now_ns = monotonic_ns()
        if raw_value is None:
            return EvidenceEntry(
                slot_id=slot_id,
                value_json=None,
                authority_source="materializer",
                received_mono_ns=now_ns,
                received_wall_time=wall_time_iso(),
                revision=(previous.revision + 1) if previous else 1,
                valid_state=ValidState.MISSING,
                reason_codes=["missing_observation"],
            )

        value_json = self._normalize_value(raw_value.value)
        revision = 1
        field = self._fields[slot_id]
        if previous:
            changed = (
                previous.value_json != value_json
                or previous.frame_id != raw_value.frame_id
                or previous.source_header_stamp != raw_value.source_header_stamp
                or previous.reason_codes != raw_value.reason_codes
            )
            if field.revision_rule == "increment_on_every_accepted_sample" and changed:
                revision = previous.revision + 1
            elif field.revision_rule != "increment_on_every_accepted_sample" and changed:
                revision = previous.revision + 1
            else:
                revision = previous.revision
        return EvidenceEntry(
            slot_id=slot_id,
            value_json=value_json,
            authority_source=raw_value.authority_source,
            received_mono_ns=now_ns,
            received_wall_time=wall_time_iso(),
            source_header_stamp=raw_value.source_header_stamp,
            revision=revision,
            frame_id=raw_value.frame_id or value_json.get("frame_id"),
            valid_state=ValidState.VALID,
            reason_codes=raw_value.reason_codes,
        )

    def _normalize_value(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return {"value": value}
