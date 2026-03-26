from __future__ import annotations

from control_view.contracts.models import EvidenceEntry
from control_view.storage.sqlite_store import SQLiteStore


class SnapshotRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def upsert(self, entry: EvidenceEntry) -> None:
        self._store.upsert_evidence(entry)

    def get(self, slot_id: str) -> EvidenceEntry | None:
        return self._store.get_evidence(slot_id)

    def get_many(self, slot_ids: list[str]) -> dict[str, EvidenceEntry]:
        return self._store.get_evidence_many(slot_ids)

