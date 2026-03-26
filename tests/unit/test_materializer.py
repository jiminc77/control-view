from __future__ import annotations

from pathlib import Path

from control_view.backend.base import BackendSlotValue
from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import ValidState
from control_view.contracts.loader import load_contract_bundle
from control_view.contracts.models import EvidenceEntry
from control_view.runtime.event_bus import EventBus
from control_view.runtime.governor import evaluate_expression
from control_view.runtime.materializer import Materializer
from control_view.storage.ledger import LedgerRepository
from control_view.storage.snapshots import SnapshotRepository
from control_view.storage.sqlite_store import SQLiteStore

ROOT = Path(__file__).resolve().parents[2]


class StampChangingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self._stamp_index = 0

    def refresh_slot(self, slot_id: str) -> BackendSlotValue | None:
        if slot_id != "vehicle.connected":
            return super().refresh_slot(slot_id)
        self._stamp_index += 1
        return BackendSlotValue(
            value=True,
            authority_source="stamp_backend",
            source_header_stamp=f"1.{self._stamp_index:09d}",
        )

    def get_current_snapshot(self, slot_ids: list[str]) -> dict[str, BackendSlotValue | None]:
        return {slot_id: self.refresh_slot(slot_id) for slot_id in slot_ids}


def build_materializer(backend: FakeBackend) -> Materializer:
    bundle = load_contract_bundle(ROOT)
    store = SQLiteStore()
    ledger = LedgerRepository(store)
    return Materializer(
        bundle.fields,
        backend,
        SnapshotRepository(store),
        EventBus(ledger),
    )


def test_increment_on_change_ignores_header_stamp_only_updates() -> None:
    backend = StampChangingBackend()
    materializer = build_materializer(backend)

    first = materializer.refresh_slots(["vehicle.connected"])["vehicle.connected"]
    second = materializer.refresh_slots(["vehicle.connected"])["vehicle.connected"]

    assert first.value_json == {"value": True}
    assert second.value_json == {"value": True}
    assert first.revision == 1
    assert second.revision == 1


def test_expression_with_missing_nested_field_returns_false() -> None:
    evidence_map = {
        "nav.progress": EvidenceEntry(
            slot_id="nav.progress",
            value_json={"distance_m": 1.5},
            authority_source="sidecar",
            received_mono_ns=1,
            received_wall_time="2026-03-26T00:00:00Z",
            revision=1,
            valid_state=ValidState.VALID,
        )
    }

    assert evaluate_expression('nav.progress.phase == "HOLDING"', evidence_map) is False
