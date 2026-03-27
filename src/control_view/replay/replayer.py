from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from control_view.baselines import apply_baseline_policy, normalize_baseline_name
from control_view.contracts.models import LeaseToken
from control_view.replay.oracle import RuleBasedOracle
from control_view.replay.recorder import ReplayRecord
from control_view.runtime.action_state import ack_state_for_family
from control_view.service import ControlViewService


class ReplayRunner:
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
        b2_ttl_sec: float = 5.0,
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        latest_leases: dict[str, dict[str, Any]] = {}
        latest_args: dict[str, dict[str, Any]] = {}
        b2_cache: dict[str, dict[str, Any]] = {}
        serialized = [record.model_dump(mode="json") for record in records]
        normalized_event_history: list[dict[str, Any]] = []
        artifact_revision_history: dict[str, list[int]] = {}
        normalized_policy = normalize_baseline_name(policy_swap)
        if fault_injector is not None and fault_name:
            serialized = fault_injector.apply(serialized, fault_name, **(fault_params or {}))
        available_record_types = {
            record.get("record_type")
            for record in serialized
            if isinstance(record.get("record_type"), str)
        }
        previous_mono_ns: int | None = None
        active_oracle = oracle or RuleBasedOracle()

        for record in serialized:
            self._update_histories(
                record,
                normalized_event_history=normalized_event_history,
                artifact_revision_history=artifact_revision_history,
            )
            current_mono_ns = int(record.get("recorded_mono_ns", 0))
            delay_ms = 0.0
            if previous_mono_ns is not None:
                delay_ms = max(current_mono_ns - previous_mono_ns, 0) / 1_000_000
                if mode == "original" and delay_ms > 0:
                    time.sleep(delay_ms / 1000.0 / max(speed, 1e-9))
            previous_mono_ns = current_mono_ns

            output = self._replay_record(
                record,
                latest_args,
                latest_leases,
                normalized_policy=normalized_policy,
                slot_ablation=slot_ablation or [],
                b2_ttl_sec=b2_ttl_sec,
                b2_cache=b2_cache,
                current_mono_ns=current_mono_ns,
                available_record_types=available_record_types,
            )
            if output is None:
                continue
            output["scheduled_delay_ms"] = round(delay_ms, 3)
            if output.get("record_type") != "control_view_result" or output.get("legacy_trace"):
                apply_baseline_policy(output, normalized_policy)
            if record.get("fault_injection"):
                output["fault_injection"] = record["fault_injection"]
            if output.get("legacy_trace") and slot_ablation:
                self._apply_slot_ablation(output, slot_ablation)
            if output.get("family") and "verdict" in output:
                oracle_input = self._build_oracle_input(
                    output,
                    record,
                    normalized_event_history=normalized_event_history,
                    artifact_revision_history=artifact_revision_history,
                )
                oracle_decision = active_oracle.evaluate(output["family"], oracle_input)
                output["oracle_verdict"] = oracle_decision.verdict
                output["oracle_blockers"] = oracle_decision.blockers
                output["oracle_canonical_args"] = oracle_decision.canonical_args
                output["oracle_labels"] = oracle_decision.labels
            outputs.append(output)
            if single_step_count is not None and len(outputs) >= single_step_count:
                break
        return outputs

    def _replay_record(
        self,
        record: dict[str, Any],
        latest_args: dict[str, dict[str, Any]],
        latest_leases: dict[str, dict[str, Any]],
        *,
        normalized_policy: str,
        slot_ablation: list[str],
        b2_ttl_sec: float,
        b2_cache: dict[str, dict[str, Any]],
        current_mono_ns: int,
        available_record_types: set[str],
    ) -> dict[str, Any] | None:
        record_type = record["record_type"]
        family = record.get("family")
        if record_type == "control_view_request":
            if "control_view_result" in available_record_types:
                return None
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
        if record_type == "control_view_result":
            payload = dict(record.get("payload", {}))
            if family and "family" not in payload:
                payload["family"] = family
            decision_context = payload.get("decision_context")
            if family and isinstance(decision_context, dict):
                if normalized_policy == "B2":
                    self._update_b2_cache(
                        b2_cache,
                        decision_context,
                        ttl_sec=b2_ttl_sec,
                    )
                    cached_slots = self._active_b2_cache(
                        b2_cache,
                        evaluation_mono_ns=int(
                            decision_context.get("evaluation_mono_ns") or current_mono_ns
                        ),
                        ttl_sec=b2_ttl_sec,
                    )
                else:
                    cached_slots = None
                replayed = self._service.replay_view_result(
                    family,
                    decision_context,
                    baseline=normalized_policy,
                    slot_ablation=slot_ablation,
                    b2_ttl_sec=b2_ttl_sec,
                    cached_slots=cached_slots,
                )
                merged = {
                    "record_type": record_type,
                    "family": family,
                    **self._passthrough_payload(payload),
                    **replayed,
                }
                latest_args[family] = dict(merged.get("canonical_args", {}))
                lease_token = merged.get("lease_token")
                if isinstance(lease_token, dict):
                    latest_leases[family] = lease_token
                return merged
            if family:
                latest_args[family] = dict(payload.get("canonical_args", {}))
                lease_token = payload.get("lease_token")
                if isinstance(lease_token, dict):
                    latest_leases[family] = lease_token
            return {
                "record_type": record_type,
                "family": family,
                "legacy_trace": True,
                **payload,
            }
        if record_type == "execute_guarded_request" and family:
            if {"execution_result", "action_transition"} & available_record_types:
                return None
            lease_payload = latest_leases.get(family) or record["payload"]["lease_token"]
            lease = LeaseToken.model_validate(lease_payload)
            replay_now_ns = max(current_mono_ns, int(record.get("recorded_mono_ns") or 0))
            if replay_now_ns > lease.expires_mono_ns:
                refreshed = self._service._evaluate_family(
                    family,
                    record.get("payload", {}).get("canonical_args", latest_args.get(family, {})),
                    refresh=True,
                    canonical_input=True,
                )
                if refreshed.lease_token is not None:
                    lease = refreshed.lease_token
            canonical_args = record.get("payload", {}).get(
                "canonical_args",
                latest_args.get(family, {}),
            )
            if replay_now_ns > lease.expires_mono_ns:
                return {
                    "record_type": record_type,
                    "family": family,
                    "status": "EXPIRED",
                    "action_id": f"replay-expired:{family.lower()}:{replay_now_ns}",
                    "opened_obligation_ids": [],
                    "abort_reason": "lease_expired",
                }
            result = {
                "status": ack_state_for_family(family).value,
                "action_id": f"replay:{family.lower()}:{replay_now_ns}",
                "opened_obligation_ids": [],
                "abort_reason": None,
                "canonical_args": canonical_args,
            }
            return {
                "record_type": record_type,
                "family": family,
                **result,
            }
        if record_type in {
            "control_view_result",
            "execution_result",
            "action_transition",
            "obligation_transition",
            "ledger_snapshot",
            "mission_boundary",
            "normalized_event",
            "artifact_revision",
        }:
            return {
                "record_type": record_type,
                "family": family,
                **record.get("payload", {}),
            }
        return None

    def _build_oracle_input(
        self,
        output: dict[str, Any],
        record: dict[str, Any],
        *,
        normalized_event_history: list[dict[str, Any]],
        artifact_revision_history: dict[str, list[int]],
    ) -> dict[str, Any]:
        payload = record.get("payload", {})
        decision_context = payload.get("decision_context", {}) if isinstance(payload, dict) else {}
        if not isinstance(decision_context, dict):
            decision_context = {}
        return {
            "evidence_map": deepcopy(decision_context.get("evidence_map", {})),
            "critical_slots": output.get("critical_slots", {}),
            "support_slots": output.get("support_slots", {}),
            "full_state": payload.get("full_state", {}),
            "canonical_args": output.get("canonical_args", {}),
            "recorded_canonical_args": decision_context.get("canonical_args", {}),
            "blockers": payload.get("blockers", output.get("blockers", [])),
            "open_obligations": decision_context.get(
                "open_obligations",
                output.get("open_obligations", []),
            ),
            "artifact_revisions": decision_context.get(
                "artifact_revisions",
                output.get("artifact_revisions", []),
            ),
            "artifact_revision_map": decision_context.get("artifact_revision_map", {}),
            "artifact_revision_history": {
                key: list(values)
                for key, values in artifact_revision_history.items()
            },
            "normalized_event_history": list(normalized_event_history),
            "commit_guard_slots": output.get(
                "commit_guard_slots",
                decision_context.get("commit_guard_slots", []),
            ),
            "commit_guard_revisions": output.get(
                "commit_guard_revisions",
                decision_context.get("commit_guard_revisions", {}),
            ),
            "fault_name": (
                record.get("fault_injection", {}) or {}
            ).get("fault_name"),
            "reason_code": (record.get("fault_injection", {}) or {}).get("reason_code"),
            "ground_truth_source": output.get(
                "ground_truth_source",
                decision_context.get("ground_truth_source"),
            ),
            "legacy_trace": bool(output.get("legacy_trace")),
        }

    def _update_histories(
        self,
        record: dict[str, Any],
        *,
        normalized_event_history: list[dict[str, Any]],
        artifact_revision_history: dict[str, list[int]],
    ) -> None:
        record_type = record.get("record_type")
        payload = record.get("payload", {})
        if record_type == "normalized_event" and isinstance(payload, dict):
            normalized_event_history.append(payload)
        if record_type == "artifact_revision" and isinstance(payload, dict):
            artifact_name = payload.get("artifact_name")
            revision = payload.get("revision")
            if isinstance(artifact_name, str) and revision is not None:
                artifact_revision_history.setdefault(artifact_name, []).append(int(revision))

    def _apply_slot_ablation(self, output: dict[str, Any], slot_ids: list[str]) -> None:
        for bucket_name in ("critical_slots", "support_slots"):
            bucket = output.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            for slot_id in slot_ids:
                bucket.pop(slot_id, None)
        output["ablated_slots"] = sorted(set(slot_ids))

    def _passthrough_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        passthrough = deepcopy(payload)
        for key in {
            "verdict",
            "canonical_args",
            "critical_slots",
            "support_slots",
            "blockers",
            "open_obligations",
            "commit_guard_slots",
            "commit_guard_revisions",
            "lease_token",
            "lease_expires_in_ms",
        }:
            passthrough.pop(key, None)
        return passthrough

    def _update_b2_cache(
        self,
        cache: dict[str, dict[str, Any]],
        decision_context: dict[str, Any],
        *,
        ttl_sec: float,
    ) -> None:
        evidence_map = decision_context.get("evidence_map", {})
        if not isinstance(evidence_map, dict):
            return
        for slot_id, entry in evidence_map.items():
            if isinstance(entry, dict):
                cache[str(slot_id)] = deepcopy(entry)
        self._active_b2_cache(
            cache,
            evaluation_mono_ns=int(decision_context.get("evaluation_mono_ns") or 0),
            ttl_sec=ttl_sec,
        )

    def _active_b2_cache(
        self,
        cache: dict[str, dict[str, Any]],
        *,
        evaluation_mono_ns: int,
        ttl_sec: float,
    ) -> dict[str, dict[str, Any]]:
        ttl_ns = int(float(ttl_sec) * 1_000_000_000)
        active: dict[str, dict[str, Any]] = {}
        expired: list[str] = []
        for slot_id, entry in cache.items():
            if not isinstance(entry, dict):
                expired.append(slot_id)
                continue
            received_mono_ns = int(entry.get("received_mono_ns") or 0)
            if evaluation_mono_ns and (evaluation_mono_ns - received_mono_ns) > ttl_ns:
                expired.append(slot_id)
                continue
            active[slot_id] = deepcopy(entry)
        for slot_id in expired:
            cache.pop(slot_id, None)
        return active
