#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from control_view.backend.mavros_backend import MavrosBackend
from control_view.common.types import ActionState, Verdict
from control_view.replay.metrics import compute_metrics
from control_view.replay.recorder import ReplayRecorder
from control_view.service import ControlViewService

TERMINAL_STATES = {
    ActionState.CONFIRMED,
    ActionState.FAILED,
    ActionState.EXPIRED,
    ActionState.ABORTED,
}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text())
    return loaded or {}


def _git_rev(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _wait_for_act(
    service: ControlViewService,
    family: str,
    proposed_args: dict[str, Any] | None,
    *,
    timeout_sec: float,
):
    deadline = time.monotonic() + timeout_sec
    last_result = None
    while time.monotonic() < deadline:
        result = service.get_control_view(family, proposed_args or {})
        last_result = result
        if result.verdict == Verdict.ACT:
            return result
        if result.verdict == Verdict.REFRESH:
            time.sleep(0.25)
            continue
        blocker_text = ", ".join(blocker.message for blocker in result.blockers)
        raise RuntimeError(f"{family} cannot proceed: {result.verdict.value} ({blocker_text})")
    raise TimeoutError(
        f"timed out waiting for {family} to become ACT; "
        f"last verdict={getattr(last_result, 'verdict', 'unknown')}"
    )


def _wait_for_terminal_action(
    service: ControlViewService,
    action_id: str,
    *,
    poll_family: str,
    poll_args: dict[str, Any] | None = None,
    timeout_sec: float,
):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        service.get_control_view(poll_family, poll_args or {})
        action = service.store.get_action(action_id)
        if action is not None and action.state in TERMINAL_STATES:
            return action
        time.sleep(0.25)
    action = service.store.get_action(action_id)
    raise TimeoutError(
        f"timed out waiting for {action_id}; "
        f"last_state={getattr(action, 'state', 'missing')}"
    )


def _run_family(
    service: ControlViewService,
    family: str,
    proposed_args: dict[str, Any] | None,
    *,
    readiness_timeout_sec: float,
    terminal_timeout_sec: float,
):
    view = _wait_for_act(
        service,
        family,
        proposed_args,
        timeout_sec=readiness_timeout_sec,
    )
    result = service.execute_guarded(family, view.canonical_args, view.lease_token)
    if result.status in {ActionState.FAILED, ActionState.EXPIRED, ActionState.ABORTED}:
        raise RuntimeError(f"{family} failed before confirmation: {result.model_dump(mode='json')}")
    terminal_timeout = terminal_timeout_sec
    if "land_timeout_sec" in view.canonical_args:
        terminal_timeout = max(
            terminal_timeout_sec,
            float(view.canonical_args["land_timeout_sec"]) + 5.0,
        )
    action = _wait_for_terminal_action(
        service,
        result.action_id,
        poll_family=family,
        poll_args=proposed_args,
        timeout_sec=terminal_timeout,
    )
    if action.state != ActionState.CONFIRMED:
        raise RuntimeError(f"{family} ended in {action.state.value}")
    return action


def _current_position(service: ControlViewService) -> dict[str, float]:
    pose_entry = service.materializer.refresh_slots(["pose.local"])["pose.local"]
    position = pose_entry.value_json.get("position", {})
    return {
        "x": float(position.get("x", 0.0)),
        "y": float(position.get("y", 0.0)),
        "z": float(position.get("z", 0.0)),
    }


def _current_mode(service: ControlViewService) -> str:
    mode_entry = service.materializer.refresh_slots(["vehicle.mode"])["vehicle.mode"]
    return str(mode_entry.value_json.get("value", ""))


def _current_speed_mps(service: ControlViewService) -> float:
    velocity_entry = service.materializer.refresh_slots(["velocity.local"])["velocity.local"]
    linear = velocity_entry.value_json.get("linear", velocity_entry.value_json)
    return (
        float(linear.get("x", 0.0)) ** 2
        + float(linear.get("y", 0.0)) ** 2
        + float(linear.get("z", 0.0)) ** 2
    ) ** 0.5


def _wait_until_takeoff_stabilizes(service: ControlViewService, *, timeout_sec: float) -> None:
    deadline = time.monotonic() + timeout_sec
    stable_started_at = None
    while time.monotonic() < deadline:
        current_mode = _current_mode(service)
        if current_mode != "AUTO.TAKEOFF":
            return
        if _current_speed_mps(service) <= 0.3:
            if stable_started_at is None:
                stable_started_at = time.monotonic()
            elif time.monotonic() - stable_started_at >= 1.0:
                return
        else:
            stable_started_at = None
        service.get_control_view("ARM")
        time.sleep(0.25)
    raise TimeoutError("vehicle did not stabilize after takeoff")


def _wait_until_holding(service: ControlViewService, *, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    stable_started_at = None
    while time.monotonic() < deadline:
        if _already_holding(service):
            if stable_started_at is None:
                stable_started_at = time.monotonic()
            elif time.monotonic() - stable_started_at >= 1.0:
                return True
        else:
            stable_started_at = None
        service.get_control_view("ARM")
        time.sleep(0.25)
    return False


def _ensure_holding(service: ControlViewService) -> None:
    if _wait_until_holding(service, timeout_sec=10.0):
        return
    _run_family(
        service,
        "HOLD",
        {},
        readiness_timeout_sec=10.0,
        terminal_timeout_sec=10.0,
    )


def _already_holding(service: ControlViewService) -> bool:
    return _current_mode(service) in {"AUTO.LOITER", "AUTO.TAKEOFF"} and _current_speed_mps(
        service
    ) <= 0.3


def _goto_args(service: ControlViewService) -> dict[str, Any]:
    position = _current_position(service)
    return {
        "target_pose": {
            "position": {
                "x": round(position["x"] + 2.0, 3),
                "y": round(position["y"], 3),
                "z": round(max(position["z"], 3.0), 3),
            },
            "frame_id": "map",
        }
    }


def _metrics_records(recorder: ReplayRecorder) -> list[dict[str, Any]]:
    return [record.model_dump(mode="json") for record in recorder.records]


def run_mission(service: ControlViewService, mission: str) -> None:
    _run_family(
        service,
        "ARM",
        {},
        readiness_timeout_sec=10.0,
        terminal_timeout_sec=10.0,
    )
    _run_family(
        service,
        "TAKEOFF",
        {"target_altitude": 3.0},
        readiness_timeout_sec=20.0,
        terminal_timeout_sec=30.0,
    )
    try:
        _wait_until_takeoff_stabilizes(service, timeout_sec=15.0)
    except TimeoutError:
        pass
    _ensure_holding(service)
    if mission == "takeoff_hold_land":
        _run_family(
            service,
            "HOLD",
            {},
            readiness_timeout_sec=10.0,
            terminal_timeout_sec=10.0,
        )
        _run_family(
            service,
            "LAND",
            {},
            readiness_timeout_sec=10.0,
            terminal_timeout_sec=45.0,
        )
        return

    goto_args = _goto_args(service)
    _run_family(
        service,
        "GOTO",
        goto_args,
        readiness_timeout_sec=20.0,
        terminal_timeout_sec=40.0,
    )
    if mission == "goto_hold_land":
        _run_family(
            service,
            "HOLD",
            {},
            readiness_timeout_sec=10.0,
            terminal_timeout_sec=10.0,
        )
        _run_family(
            service,
            "LAND",
            {},
            readiness_timeout_sec=10.0,
            terminal_timeout_sec=45.0,
        )
        return
    if mission == "goto_rtl":
        _run_family(
            service,
            "RTL",
            {},
            readiness_timeout_sec=10.0,
            terminal_timeout_sec=20.0,
        )
        return
    raise ValueError(f"unknown mission: {mission}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--backend-config",
        type=Path,
        default=Path("configs/backend_mavros.yaml"),
    )
    parser.add_argument(
        "--mission",
        choices=["takeoff_hold_land", "goto_hold_land", "goto_rtl"],
        required=True,
    )
    parser.add_argument("--sqlite-path", default=":memory:")
    parser.add_argument("--record-jsonl", type=Path, default=None)
    parser.add_argument("--summary-json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    backend_config = args.backend_config
    if not backend_config.is_absolute():
        backend_config = root / backend_config
    recorder = ReplayRecorder()
    service = ControlViewService(
        root,
        backend=MavrosBackend(_load_yaml(backend_config)),
        sqlite_path=args.sqlite_path,
        recorder=recorder,
    )
    record_jsonl = args.record_jsonl or (
        root / "artifacts" / "replay" / f"{args.mission}.jsonl"
    )
    summary_json = args.summary_json or (
        root / "artifacts" / "metrics" / f"{args.mission}.json"
    )
    record_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    recorder.record_mission_boundary(args.mission, "start")
    mission_success = False
    failure_message: str | None = None
    try:
        run_mission(service, args.mission)
        mission_success = (
            bool(service.store.list_actions())
            and not service.store.list_open_obligations()
        )
    except Exception as exc:
        failure_message = str(exc)
        raise
    finally:
        recorder.record_mission_boundary(
            args.mission,
            "end",
            payload={
                "success": mission_success,
                "open_obligation_count": len(service.store.list_open_obligations()),
                "action_count": len(service.store.list_actions(limit=1000)),
                **({"failure_message": failure_message} if failure_message else {}),
            },
        )
        recorder.dump_jsonl(record_jsonl)
        if hasattr(service.backend, "shutdown"):
            service.backend.shutdown()
    summary = {
        "mission": args.mission,
        "control_view_git_rev": _git_rev(root),
        "px4_git_rev": _git_rev(root.parent / "PX4-Autopilot"),
        "actions": [
            action.model_dump(mode="json")
            for action in service.store.list_actions(limit=50)
        ],
        "open_obligations": [
            obligation.model_dump(mode="json")
            for obligation in service.store.list_open_obligations()
        ],
        "artifact_revisions": service.artifacts.list_all(),
        "metrics": compute_metrics(_metrics_records(recorder)),
        "record_jsonl": str(record_jsonl),
    }
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
