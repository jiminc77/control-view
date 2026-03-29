from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from control_view.common.utils import stable_json_dumps
from control_view.contracts.models import ActionRecord, EvidenceEntry, ObligationRecord


class SQLiteStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        with self._lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cursor = self.connection.cursor()
            cursor.executescript(
                """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                received_mono_ns INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                record_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence_current (
                slot_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                record_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS obligations (
                obligation_id TEXT PRIMARY KEY,
                family TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_mono_ns INTEGER NOT NULL,
                record_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY,
                family TEXT NOT NULL,
                state TEXT NOT NULL,
                requested_mono_ns INTEGER NOT NULL,
                record_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_name TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_received_mono_ns
                ON events(received_mono_ns);
            CREATE INDEX IF NOT EXISTS idx_obligations_status_family
                ON obligations(status, family);
            CREATE INDEX IF NOT EXISTS idx_actions_state
                ON actions(state);
            CREATE INDEX IF NOT EXISTS idx_artifacts_name_revision
                ON artifacts(artifact_name, revision);
            """
            )
            self.connection.commit()

    def _upsert_model(
        self,
        table: str,
        key_column: str,
        key_value: str,
        payload: BaseModel,
        **extras: Any,
    ) -> None:
        columns = [key_column, *extras.keys(), "record_json"]
        values = [key_value, *extras.values(), payload.model_dump_json()]
        placeholders = ", ".join(["?"] * len(columns))
        assignments = ", ".join(f"{column} = excluded.{column}" for column in columns[1:])
        with self._lock:
            self.connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT({key_column}) DO UPDATE SET {assignments}",
                values,
            )
            self.connection.commit()

    def append_event(self, event) -> None:
        with self._lock:
            self.connection.execute(
                """
            INSERT INTO events (
                event_id,
                event_type,
                source,
                received_mono_ns,
                payload_json,
                record_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    event.event_id,
                    event.event_type,
                    event.source,
                    event.received_mono_ns,
                    stable_json_dumps(event.payload_json),
                    event.model_dump_json(),
                ),
            )
            self.connection.commit()

    def upsert_evidence(self, entry: EvidenceEntry) -> None:
        self._upsert_model(
            "evidence_current",
            "slot_id",
            entry.slot_id,
            entry,
            revision=entry.revision,
        )

    def get_evidence(self, slot_id: str) -> EvidenceEntry | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT record_json FROM evidence_current WHERE slot_id = ?",
                (slot_id,),
            ).fetchone()
        if not row:
            return None
        return EvidenceEntry.model_validate_json(row["record_json"])

    def get_evidence_many(self, slot_ids: list[str]) -> dict[str, EvidenceEntry]:
        return {slot_id: entry for slot_id in slot_ids if (entry := self.get_evidence(slot_id))}

    def upsert_action(self, record: ActionRecord) -> None:
        self._upsert_model(
            "actions",
            "action_id",
            record.action_id,
            record,
            family=record.family,
            state=record.state.value,
            requested_mono_ns=record.requested_mono_ns,
        )

    def list_actions(self, limit: int = 20) -> list[ActionRecord]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT record_json FROM actions ORDER BY requested_mono_ns DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ActionRecord.model_validate_json(row["record_json"]) for row in rows]

    def list_actions_since(self, since_mono_ns: int) -> list[ActionRecord]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT record_json FROM actions WHERE requested_mono_ns >= ? "
                "ORDER BY requested_mono_ns DESC",
                (since_mono_ns,),
            ).fetchall()
        return [ActionRecord.model_validate_json(row["record_json"]) for row in rows]

    def get_action(self, action_id: str) -> ActionRecord | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT record_json FROM actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        if not row:
            return None
        return ActionRecord.model_validate_json(row["record_json"])

    def upsert_obligation(self, record: ObligationRecord) -> None:
        self._upsert_model(
            "obligations",
            "obligation_id",
            record.obligation_id,
            record,
            family=record.family,
            status=record.status,
            updated_mono_ns=record.updated_mono_ns,
        )

    def list_open_obligations(self) -> list[ObligationRecord]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT record_json FROM obligations "
                "WHERE status = 'OPEN' "
                "ORDER BY updated_mono_ns DESC"
            ).fetchall()
        return [ObligationRecord.model_validate_json(row["record_json"]) for row in rows]

    def list_obligations_for_action(self, action_id: str) -> list[ObligationRecord]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT record_json FROM obligations "
                "WHERE json_extract(record_json, '$.related_action_id') = ? "
                "ORDER BY updated_mono_ns DESC",
                (action_id,),
            ).fetchall()
        return [ObligationRecord.model_validate_json(row["record_json"]) for row in rows]

    def tail_events(self, last_n: int = 20) -> list:
        with self._lock:
            rows = self.connection.execute(
                "SELECT record_json FROM events ORDER BY received_mono_ns DESC LIMIT ?",
                (last_n,),
            ).fetchall()
        from control_view.runtime.event_bus import NormalizedEvent

        return [NormalizedEvent.model_validate_json(row["record_json"]) for row in rows]

    def tail_events_since(self, since_mono_ns: int) -> list:
        with self._lock:
            rows = self.connection.execute(
                "SELECT record_json FROM events WHERE received_mono_ns >= ? "
                "ORDER BY received_mono_ns DESC",
                (since_mono_ns,),
            ).fetchall()
        from control_view.runtime.event_bus import NormalizedEvent

        return [NormalizedEvent.model_validate_json(row["record_json"]) for row in rows]

    def upsert_artifact(self, artifact_name: str, revision: int, payload: dict[str, Any]) -> None:
        with self._lock:
            self.connection.execute(
                """
            INSERT INTO artifacts (artifact_name, revision, payload_json) VALUES (?, ?, ?)
            ON CONFLICT(artifact_name) DO UPDATE SET
                revision = excluded.revision,
                payload_json = excluded.payload_json
            """,
                (artifact_name, revision, stable_json_dumps(payload)),
            )
            self.connection.commit()

    def list_artifacts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT artifact_name, revision, payload_json "
                "FROM artifacts ORDER BY artifact_name ASC"
            ).fetchall()
        return [
            {
                "artifact_name": row["artifact_name"],
                "revision": row["revision"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def get_artifact(self, artifact_name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT artifact_name, revision, payload_json "
                "FROM artifacts WHERE artifact_name = ?",
                (artifact_name,),
            ).fetchone()
        if not row:
            return None
        return {
            "artifact_name": row["artifact_name"],
            "revision": row["revision"],
            "payload": json.loads(row["payload_json"]),
        }
