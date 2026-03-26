from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from control_view.common.time import monotonic_ns, wall_time_iso


class ReplayRecord(BaseModel):
    record_type: str
    family: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_mono_ns: int
    recorded_wall_time: str


class ReplayRecorder:
    def __init__(self) -> None:
        self.records: list[ReplayRecord] = []

    def record(
        self,
        record_type: str,
        *,
        family: str | None = None,
        payload: dict[str, Any],
    ) -> ReplayRecord:
        record = ReplayRecord(
            record_type=record_type,
            family=family,
            payload=payload,
            recorded_mono_ns=monotonic_ns(),
            recorded_wall_time=wall_time_iso(),
        )
        self.records.append(record)
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
