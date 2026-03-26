from __future__ import annotations

import time
from typing import Any

from control_view.contracts.models import LeaseToken
from control_view.replay.oracle import RuleBasedOracle
from control_view.replay.recorder import ReplayRecord
from control_view.runtime.action_state import ack_state_for_family
from control_view.service import ControlViewService


class ReplayRunner:
    _HIGH_RISK = {"TAKEOFF", "GOTO", "RTL", "LAND"}
    _B2_IGNORED_BLOCKERS = {
        "missing_slot",
        "stale_slot",
        "invalidated_slot",
        "disagreed_slot",
        "unconfirmed_slot",
        "pending_transition",
    }
    _B3_IGNORED_BLOCKERS = {
        "invalidated_slot",
        "disagreed_slot",
        "pending_transition",
    }

    def __init__(self, service: ControlViewService) -> None:
        self._service = service

    def replay(
        self,
        records: list[ReplayRecord],
        *,
        mode: str = "fast_forward",
        speed: float = 1.0,
        single_step_count: int | None = None,
        fault_injector=None,
        fault_name: str | None = None,
        fault_params: dict[str, Any] | None = None,
        oracle: RuleBasedOracle | None = None,
        slot_ablation: list[str] | None = None,
        policy_swap: str | None = None,
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        latest_leases: dict[str, dict[str, Any]] = {}
        latest_args: dict[str, dict[str, Any]] = {}
        serialized = [record.model_dump(mode="json") for record in records]
        if fault_injector is not None and fault_name:
            serialized = fault_injector.apply(serialized, fault_name, **(fault_params or {}))
        previous_mono_ns: int | None = None
        active_oracle = oracle or RuleBasedOracle()

        for index, record in enumerate(serialized):
            if single_step_count is not None and index >= single_step_count:
                break
            current_mono_ns = int(record.get("recorded_mono_ns", 0))
            delay_ms = 0.0
            if previous_mono_ns is not None:
                delay_ms = max(current_mono_ns - previous_mono_ns, 0) / 1_000_000
                if mode == "original" and delay_ms > 0:
                    time.sleep(delay_ms / 1000.0 / max(speed, 1e-9))
            previous_mono_ns = current_mono_ns

            output = self._replay_record(record, latest_args, latest_leases)
            if output is None:
                continue
            output["scheduled_delay_ms"] = round(delay_ms, 3)
            if policy_swap:
                self._apply_policy_swap(output, policy_swap)
                output["policy_swap"] = policy_swap
            if record.get("fault_injection"):
                output["fault_injection"] = record["fault_injection"]
            if slot_ablation:
                self._apply_slot_ablation(output, slot_ablation)
            if output.get("family"):
                oracle_input = self._build_oracle_input(output, record)
                oracle_decision = active_oracle.evaluate(output["family"], oracle_input)
                output["oracle_verdict"] = oracle_decision.verdict
                output["oracle_blockers"] = oracle_decision.blockers
            outputs.append(output)
        return outputs

    def _replay_record(
        self,
        record: dict[str, Any],
        latest_args: dict[str, dict[str, Any]],
        latest_leases: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        record_type = record["record_type"]
        family = record.get("family")
        if record_type == "control_view_request":
            result = self._service.get_control_view(
                family,
                record.get("payload", {}).get("proposed_args", {}),
            )
            latest_args[family or ""] = result.canonical_args
            latest_leases[family or ""] = (
                result.lease_token.model_dump(mode="json") if result.lease_token else {}
            )
            return {
                "record_type": record_type,
                "family": family,
                **result.model_dump(mode="json"),
            }
        if record_type == "execute_guarded_request" and family:
            result = self._service.execute_guarded(
                family,
                record.get("payload", {}).get("canonical_args", latest_args.get(family, {})),
                LeaseToken.model_validate(record["payload"]["lease_token"]),
            )
            return {
                "record_type": record_type,
                "family": family,
                **result.model_dump(mode="json"),
            }
        if record_type in {
            "control_view_result",
            "execution_result",
            "action_transition",
            "ledger_snapshot",
        }:
            return {
                "record_type": record_type,
                "family": family,
                **record.get("payload", {}),
            }
        return None

    def _build_oracle_input(self, output: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        return {
            "critical_slots": output.get("critical_slots", {}),
            "support_slots": output.get("support_slots", {}),
            "full_state": record.get("payload", {}).get("full_state", {}),
            "canonical_args": output.get("canonical_args", {}),
            "blockers": output.get("blockers", []),
            "open_obligations": output.get("open_obligations", []),
        }

    def _apply_slot_ablation(self, output: dict[str, Any], slot_ids: list[str]) -> None:
        for bucket_name in ("critical_slots", "support_slots"):
            bucket = output.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            for slot_id in slot_ids:
                bucket.pop(slot_id, None)
        output["ablated_slots"] = sorted(set(slot_ids))

    def _apply_policy_swap(self, output: dict[str, Any], policy_swap: str) -> None:
        if policy_swap == "B4":
            return
        if "verdict" in output and "blockers" in output:
            ignored_blockers = self._ignored_blockers_for(policy_swap)
            blockers = [
                blocker
                for blocker in output.get("blockers", [])
                if blocker.get("kind") not in ignored_blockers
            ]
            output["blockers"] = blockers
            if "pending_transition" in ignored_blockers:
                output["open_obligations"] = []
            output["verdict"] = self._policy_verdict(
                output.get("family", ""),
                blockers,
            )
        if (
            "status" in output
            and policy_swap in {"B2", "B3"}
            and output.get("status") == "ABORTED"
        ):
            output["status"] = ack_state_for_family(output.get("family", "")).value
            output["abort_reason"] = None

    def _ignored_blockers_for(self, policy_swap: str) -> set[str]:
        if policy_swap == "B2":
            return self._B2_IGNORED_BLOCKERS
        if policy_swap == "B3":
            return self._B3_IGNORED_BLOCKERS
        return set()

    def _policy_verdict(self, family: str, blockers: list[dict[str, Any]]) -> str:
        if not blockers:
            return "ACT"
        if all(blocker.get("refreshable", False) for blocker in blockers):
            return "REFRESH"
        if family in self._HIGH_RISK:
            return "SAFE_HOLD"
        return "REFUSE"
