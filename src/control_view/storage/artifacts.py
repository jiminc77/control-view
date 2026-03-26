from __future__ import annotations

from typing import Any

from control_view.storage.sqlite_store import SQLiteStore


class ArtifactRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def upsert(self, artifact_name: str, revision: int, payload: dict[str, Any]) -> None:
        self._store.upsert_artifact(artifact_name, revision, payload)

    def get(self, artifact_name: str) -> dict[str, Any] | None:
        return self._store.get_artifact(artifact_name)

    def list_all(self) -> list[dict[str, Any]]:
        return self._store.list_artifacts()
