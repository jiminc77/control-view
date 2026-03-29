from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from control_view.common.time import monotonic_ns, wall_time_iso


class ReplayRecord(BaseModel):
    record_type: str
    family: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_mono_ns: int
    recorded_wall_time: str
    source_header_stamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplayRecorder:
    def __init__(
        self,
        *,
        default_metadata: dict[str, Any] | None = None,
        stream_path: str | Path | None = None,
    ) -> None:
        self.records: list[ReplayRecord] = []
        self.default_metadata = {
            "run_id": str(uuid4()),
            **(default_metadata or {}),
        }
        self.stream_path = Path(stream_path) if stream_path is not None else None

    def record(
        self,
        record_type: str,
        *,
        family: str | None = None,
        payload: dict[str, Any],
        source_header_stamp: str | None = None,
        metadata: dict[str, Any] | None = None,
        recorded_mono_ns: int | None = None,
        recorded_wall_time: str | None = None,
    ) -> ReplayRecord:
        record = ReplayRecord(
            record_type=record_type,
            family=family,
            payload=payload,
            recorded_mono_ns=recorded_mono_ns or monotonic_ns(),
            recorded_wall_time=recorded_wall_time or wall_time_iso(),
            source_header_stamp=source_header_stamp,
            metadata={
                **self.default_metadata,
                **(metadata or {}),
            },
        )
        self.records.append(record)
        if self.stream_path is not None:
            self.stream_path.parent.mkdir(parents=True, exist_ok=True)
            with self.stream_path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")
        return record

    def record_view_request(self, family: str, proposed_args: dict[str, Any]) -> ReplayRecord:
        return self.record(
            "control_view_request",
            family=family,
            payload={"proposed_args": proposed_args},
        )

    def record_view_result(self, family: str, result: dict[str, Any]) -> ReplayRecord:
        return self.record(
            "control_view_result",
            family=family,
            payload=result,
        )

    def record_execution_result(self, family: str, result: dict[str, Any]) -> ReplayRecord:
        return self.record(
            "execution_result",
            family=family,
            payload=result,
        )

    def record_action_transition(self, family: str, result: dict[str, Any]) -> ReplayRecord:
        return self.record(
            "action_transition",
            family=family,
            payload=result,
        )

    def record_obligation_transition(
        self,
        family: str,
        result: dict[str, Any],
    ) -> ReplayRecord:
        return self.record(
            "obligation_transition",
            family=family,
            payload=result,
        )

    def record_normalized_event(self, event: dict[str, Any]) -> ReplayRecord:
        return self.record(
            "normalized_event",
            payload=event,
            source_header_stamp=event.get("source_header_stamp"),
            recorded_mono_ns=event.get("received_mono_ns"),
            recorded_wall_time=event.get("received_wall_time"),
            metadata={"event_type": event.get("event_type")},
        )

    def record_mission_boundary(
        self,
        mission: str,
        phase: str,
        *,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ReplayRecord:
        return self.record(
            "mission_boundary",
            payload={
                "mission": mission,
                "phase": phase,
                **(payload or {}),
            },
            metadata={
                "mission_id": mission,
                **(metadata or {}),
            },
        )

    def record_ledger_snapshot(self, payload: dict[str, Any]) -> ReplayRecord:
        return self.record("ledger_snapshot", payload=payload)

    def record_artifact_revision(self, artifact_name: str, revision: int) -> ReplayRecord:
        return self.record(
            "artifact_revision",
            payload={"artifact_name": artifact_name, "revision": revision},
        )

    def record_execute_request(
        self,
        family: str,
        canonical_args: dict[str, Any],
        lease_token: dict[str, Any],
    ) -> ReplayRecord:
        return self.record(
            "execute_guarded_request",
            family=family,
            payload={
                "canonical_args": canonical_args,
                "lease_token": lease_token,
            },
        )

    def dump_jsonl(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(record.model_dump_json() for record in self.records)
        target.write_text(content + ("\n" if self.records else ""))

    @classmethod
    def load_jsonl(cls, path: str | Path) -> list[ReplayRecord]:
        target = Path(path)
        if not target.exists():
            return []
        return [
            ReplayRecord.model_validate(json.loads(line))
            for line in target.read_text().splitlines()
            if line
        ]
