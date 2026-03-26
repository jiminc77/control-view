from __future__ import annotations

from control_view.storage.sqlite_store import SQLiteStore


class LedgerRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def append(self, event) -> None:
        self._store.append_event(event)

    def tail(self, last_n: int = 20) -> list:
        return self._store.tail_events(last_n)
