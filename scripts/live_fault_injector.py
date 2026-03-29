#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import yaml

VEHICLE_CMD_INJECT_FAILURE = 420
FAILURE_UNITS = {
    "gyro": 0,
    "accel": 1,
    "mag": 2,
    "baro": 3,
    "gps": 4,
    "optical_flow": 5,
    "vio": 6,
    "distance_sensor": 7,
    "airspeed": 8,
    "battery": 100,
    "motor": 101,
    "servo": 102,
    "avoidance": 103,
    "rc_signal": 104,
    "mavlink_signal": 105,
}
FAILURE_TYPES = {
    "ok": 0,
    "off": 1,
    "stuck": 2,
    "garbage": 3,
    "wrong": 4,
    "slow": 5,
    "delayed": 6,
    "intermittent": 7,
}


def _normalize_fault_token(value: Any) -> str:
    if isinstance(value, bool):
        return "ok" if value else "off"
    return str(value)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text())
    return payload or {}


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
            continue
        merged[key] = value
    return merged


def _observer_event_state(path: Path, event_kind: str) -> tuple[int, bool]:
    if not path.exists():
        return 0, False
    match_count = 0
    summary_seen = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        record_type = record.get("record_type")
        payload = record.get("payload", {})
        if record_type == "observer_summary":
            summary_seen = True
        if (
            record_type == "observer_event"
            and isinstance(payload, dict)
            and str(payload.get("event_kind")) == event_kind
        ):
            match_count += 1
    return match_count, summary_seen


def _wait_for_observer_event(
    *,
    observer_jsonl: Path | None,
    event_kind: str,
    occurrence: int,
    timeout_sec: float,
) -> None:
    if observer_jsonl is None:
        raise RuntimeError("observer_jsonl is required for after_observer_event steps")
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() <= deadline:
        match_count, summary_seen = _observer_event_state(observer_jsonl, event_kind)
        if match_count >= occurrence:
            return
        if summary_seen:
            raise RuntimeError(
                f"observer completed before {event_kind} occurrence {occurrence} was observed"
            )
        time.sleep(0.05)
    raise RuntimeError(
        f"timed out waiting for {event_kind} occurrence {occurrence} in {observer_jsonl}"
    )


def _observer_event_count(path: Path | None, event_kind: str) -> int:
    if path is None:
        return 0
    match_count, _ = _observer_event_state(path, event_kind)
    return match_count


class RosFaultClient:
    def __init__(self, *, namespace: str, dry_run: bool) -> None:
        self._namespace = namespace.rstrip("/")
        self._dry_run = dry_run
        self._rclpy: Any | None = None
        self._node: Any | None = None
        self._command_client: Any | None = None
        self._set_mode_client: Any | None = None

    def close(self) -> None:
        if self._node is None or self._rclpy is None:
            return
        self._node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()

    def inject_failure(
        self,
        *,
        unit: str,
        failure_type: str,
        instance: int = 0,
    ) -> dict[str, Any]:
        if self._dry_run:
            return {
                "status": "dry_run",
                "unit": unit,
                "failure_type": failure_type,
                "instance": instance,
            }
        self._ensure_runtime()
        from mavros_msgs.srv import CommandLong  # type: ignore[import-not-found]

        request = CommandLong.Request()
        request.broadcast = False
        request.command = VEHICLE_CMD_INJECT_FAILURE
        request.confirmation = 0
        request.param1 = float(FAILURE_UNITS[unit])
        request.param2 = float(FAILURE_TYPES[failure_type])
        request.param3 = float(instance)
        response = self._call(self._command_client, request)
        return {
            "status": "ok" if bool(response.success) else "error",
            "success": bool(response.success),
            "result": int(response.result),
            "unit": unit,
            "failure_type": failure_type,
            "instance": instance,
        }

    def set_mode(self, custom_mode: str) -> dict[str, Any]:
        if self._dry_run:
            return {"status": "dry_run", "custom_mode": custom_mode}
        self._ensure_runtime()
        from mavros_msgs.srv import SetMode  # type: ignore[import-not-found]

        request = SetMode.Request()
        request.base_mode = 0
        request.custom_mode = custom_mode
        response = self._call(self._set_mode_client, request)
        return {
            "status": "ok" if bool(response.mode_sent) else "error",
            "mode_sent": bool(response.mode_sent),
            "custom_mode": custom_mode,
        }

    def _ensure_runtime(self) -> None:
        if self._node is not None:
            return
        import rclpy  # type: ignore[import-not-found]
        from mavros_msgs.srv import CommandLong, SetMode  # type: ignore[import-not-found]

        self._rclpy = rclpy
        if not rclpy.ok():
            rclpy.init(args=None)
        self._node = rclpy.create_node("control_view_live_fault_injector", namespace="")
        self._command_client = self._node.create_client(
            CommandLong,
            f"{self._namespace}/cmd/command",
        )
        self._set_mode_client = self._node.create_client(
            SetMode,
            f"{self._namespace}/set_mode",
        )
        if not self._command_client.wait_for_service(timeout_sec=10.0):
            raise RuntimeError("timed out waiting for MAVROS CommandLong service")
        if not self._set_mode_client.wait_for_service(timeout_sec=10.0):
            raise RuntimeError("timed out waiting for MAVROS SetMode service")

    def _call(self, client: Any, request: Any) -> Any:
        future = client.call_async(request)
        self._rclpy.spin_until_future_complete(self._node, future, timeout_sec=10.0)
        if not future.done():
            raise RuntimeError("service call timed out")
        return future.result()


def _artifact_update(
    *,
    artifacts_dir: Path,
    artifact: str,
    revision: int,
    payload_patch: dict[str, Any],
) -> dict[str, Any]:
    path = artifacts_dir / f"{artifact}.yaml"
    current = _load_yaml(path) if path.exists() else {}
    updated = _merge_dict(current, payload_patch)
    updated["revision"] = revision
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    return {"status": "ok", "artifact": artifact, "revision": revision, "path": str(path)}


def _execute_step(
    *,
    step: dict[str, Any],
    ros_client: RosFaultClient,
    artifacts_dir: Path,
) -> dict[str, Any]:
    action = str(step.get("action", ""))
    if action == "inject_failure":
        return ros_client.inject_failure(
            unit=_normalize_fault_token(step["unit"]),
            failure_type=_normalize_fault_token(step["failure_type"]),
            instance=int(step.get("instance", 0)),
        )
    if action == "set_mode":
        return ros_client.set_mode(str(step["custom_mode"]))
    if action == "artifact_update":
        return _artifact_update(
            artifacts_dir=artifacts_dir,
            artifact=str(step["artifact"]),
            revision=int(step["revision"]),
            payload_patch=dict(step.get("payload", {})),
        )
    raise ValueError(f"unsupported scenario action: {action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-config", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--observer-jsonl", type=Path, default=None)
    parser.add_argument("--namespace", default="/mavros")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scenario = _load_yaml(args.scenario_config)
    steps = [step for step in scenario.get("steps", []) if isinstance(step, dict)]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "scenario": scenario.get("name"),
                    "step_count": len(steps),
                    "artifacts_dir": str(args.artifacts_dir),
                    "output_jsonl": str(args.output_jsonl),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    ros_client = RosFaultClient(namespace=args.namespace, dry_run=False)
    started_mono_ns = time.monotonic_ns()
    try:
        for index, step in enumerate(steps, start=1):
            schedule_metadata: dict[str, Any] = {}
            if "after_observer_event" in step:
                event_kind = str(step["after_observer_event"])
                occurrence = int(step.get("occurrence", 1))
                timeout_sec = float(step.get("timeout_sec", 120.0))
                delay_sec = float(step.get("delay_sec", 0.0))
                _wait_for_observer_event(
                    observer_jsonl=args.observer_jsonl,
                    event_kind=event_kind,
                    occurrence=occurrence,
                    timeout_sec=timeout_sec,
                )
                if delay_sec > 0:
                    time.sleep(delay_sec)
                schedule_metadata = {
                    "after_observer_event": event_kind,
                    "delay_sec": delay_sec,
                    "occurrence": occurrence,
                }
            else:
                scheduled_sec = float(step.get("at_sec", 0.0))
                scheduled_ns = started_mono_ns + int(scheduled_sec * 1_000_000_000)
                remaining_ns = scheduled_ns - time.monotonic_ns()
                if remaining_ns > 0:
                    time.sleep(remaining_ns / 1_000_000_000)
                schedule_metadata = {"scheduled_at_sec": scheduled_sec}
            applied_mono_ns = time.monotonic_ns()
            status = "ok"
            details: dict[str, Any]
            try:
                details = _execute_step(
                    step=step,
                    ros_client=ros_client,
                    artifacts_dir=args.artifacts_dir,
                )
                expectation_failed = False
                expected_event = step.get("expect_observer_event")
                if (
                    details.get("status") == "ok"
                    and expected_event is not None
                    and args.observer_jsonl is not None
                ):
                    baseline_count = _observer_event_count(
                        args.observer_jsonl,
                        str(expected_event),
                    )
                    try:
                        _wait_for_observer_event(
                            observer_jsonl=args.observer_jsonl,
                            event_kind=str(expected_event),
                            occurrence=baseline_count + 1,
                            timeout_sec=float(step.get("expect_timeout_sec", 5.0)),
                        )
                        details["effect_observed"] = str(expected_event)
                    except RuntimeError as exc:
                        details["effect_observed"] = None
                        details["effect_timeout_error"] = str(exc)
                        expectation_failed = True
                if (
                    (details.get("status") == "error" or expectation_failed)
                    and isinstance(step.get("fallback"), dict)
                ):
                    fallback_details = _execute_step(
                        step=dict(step["fallback"]),
                        ros_client=ros_client,
                        artifacts_dir=args.artifacts_dir,
                    )
                    details["fallback"] = fallback_details
                    details["fallback_reason"] = (
                        "effect_not_observed" if expectation_failed else "primary_error"
                    )
                    details["fallback_used"] = True
                    status = str(fallback_details.get("status", details.get("status", "error")))
                else:
                    status = str(details.get("status", "ok"))
            except Exception as exc:  # noqa: BLE001
                details = {"status": "error", "error": str(exc)}
                status = "error"
                if isinstance(step.get("fallback"), dict):
                    try:
                        fallback_details = _execute_step(
                            step=dict(step["fallback"]),
                            ros_client=ros_client,
                            artifacts_dir=args.artifacts_dir,
                        )
                        details["fallback"] = fallback_details
                        details["fallback_reason"] = "exception"
                        details["fallback_used"] = True
                        status = str(fallback_details.get("status", "error"))
                    except Exception as fallback_exc:  # noqa: BLE001
                        details["fallback"] = {
                            "status": "error",
                            "error": str(fallback_exc),
                        }
                        details["fallback_reason"] = "exception"
                        details["fallback_used"] = True
            _append_jsonl(
                args.output_jsonl,
                {
                    "index": index,
                    "scenario": scenario.get("name"),
                    "note": step.get("note"),
                    "applied_mono_ns": applied_mono_ns,
                    "status": status,
                    "step": step,
                    "details": details,
                    **schedule_metadata,
                },
            )
    finally:
        ros_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
