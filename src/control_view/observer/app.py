from __future__ import annotations

import argparse
import json
import math
import signal
import threading
import time
from pathlib import Path
from typing import Any

from control_view.common.time import monotonic_ns, wall_time_iso
from control_view.observer.tracker import MissionObserverTracker, ObserverSample
from control_view.replay.recorder import ReplayRecorder


class ObserverNode:
    def __init__(
        self,
        *,
        mission: str,
        namespace: str,
        sample_period_ms: int,
        recorder: ReplayRecorder,
        fault_events_jsonl: Path | None,
        stop_when_complete: bool,
    ) -> None:
        self._mission = mission
        self._namespace = namespace
        self._sample_period_ms = sample_period_ms
        self._recorder = recorder
        self._fault_events_jsonl = fault_events_jsonl
        self._tracker = MissionObserverTracker(mission)
        self._stop_when_complete = stop_when_complete
        self._stop_event = threading.Event()
        self._started_mono_ns: int | None = None
        self._rclpy: Any | None = None
        self._node: Any | None = None
        self._executor: Any | None = None
        self._spin_thread: threading.Thread | None = None
        self._fault_event_index = 0
        self._external_fault_active = False
        self._external_fault_count = 0
        self._external_recovered_fault_count = 0
        self._external_first_fault_mono_ns: int | None = None
        self._external_first_recovery_mono_ns: int | None = None
        self._latest: dict[str, Any] = {
            "connected": False,
            "armed": False,
            "mode": "",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "speed_mps": 0.0,
            "on_ground": True,
        }

    def _external_fault_records(self) -> list[dict[str, Any]]:
        if self._fault_events_jsonl is None or not self._fault_events_jsonl.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self._fault_events_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if str(payload.get("status")) == "ok":
                records.append(payload)
        return records

    def run(self, *, duration_sec: float | None = None) -> None:
        self._ensure_runtime()
        if self._node is None:
            return
        self._create_timer()
        deadline = (
            None
            if duration_sec is None
            else monotonic_ns() + int(duration_sec * 1_000_000_000)
        )
        while not self._stop_event.is_set():
            if deadline is not None and monotonic_ns() >= deadline:
                break
            if self._stop_when_complete and self._tracker.is_complete():
                break
            time.sleep(0.1)
        self.shutdown()

    def request_stop(self) -> None:
        self._stop_event.set()

    def shutdown(self) -> None:
        summary = self._tracker.summary()
        external_fault_records = self._external_fault_records()
        external_fault_count = max(self._external_fault_count, len(external_fault_records))
        external_recovered_fault_count = self._external_recovered_fault_count
        if (
            bool(summary.get("mission_success"))
            and external_recovered_fault_count < external_fault_count
        ):
            external_recovered_fault_count = external_fault_count
        summary["fault_count"] = int(summary.get("fault_count", 0)) + external_fault_count
        summary["recovered_fault_count"] = int(
            summary.get("recovered_fault_count", 0)
        ) + external_recovered_fault_count
        first_fault_candidates = [
            value
            for value in [
                summary.get("first_fault_mono_ns"),
                self._external_first_fault_mono_ns,
                *[
                    int(record.get("applied_mono_ns"))
                    for record in external_fault_records
                    if record.get("applied_mono_ns") is not None
                ],
            ]
            if value is not None
        ]
        summary["first_fault_mono_ns"] = (
            min(first_fault_candidates) if first_fault_candidates else None
        )
        if (
            float(summary.get("time_to_first_recovery_sec") or 0.0) == 0.0
            and self._external_first_recovery_mono_ns is not None
        ):
            summary["time_to_first_recovery_sec"] = round(
                self._elapsed_sec(self._external_first_recovery_mono_ns),
                3,
            )
        self._recorder.record(
            "observer_summary",
            payload=summary,
            metadata={"mission_id": self._mission},
        )
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None
        if self._spin_thread is not None and self._spin_thread.is_alive():
            self._spin_thread.join(timeout=1.0)
        self._spin_thread = None
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        if self._rclpy is not None and self._rclpy.ok():
            self._rclpy.shutdown()

    def _ensure_runtime(self) -> None:
        if self._node is not None:
            return
        import rclpy  # type: ignore[import-not-found]
        from geometry_msgs.msg import TwistStamped
        from mavros_msgs.msg import ExtendedState, State
        from nav_msgs.msg import Odometry
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import (
            DurabilityPolicy,
            HistoryPolicy,
            QoSProfile,
            ReliabilityPolicy,
            qos_profile_sensor_data,
        )

        self._rclpy = rclpy
        if not rclpy.ok():
            rclpy.init(args=None)
        self._node = rclpy.create_node("control_view_observer", namespace="")
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._spin_thread.start()

        state_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        sensor_qos = qos_profile_sensor_data
        self._node.create_subscription(
            State,
            f"{self._namespace}/state",
            self._on_state,
            state_qos,
        )
        self._node.create_subscription(
            Odometry,
            f"{self._namespace}/local_position/odom",
            self._on_odom,
            sensor_qos,
        )
        self._node.create_subscription(
            TwistStamped,
            f"{self._namespace}/local_position/velocity_local",
            self._on_velocity,
            sensor_qos,
        )
        self._node.create_subscription(
            ExtendedState,
            f"{self._namespace}/extended_state",
            self._on_extended_state,
            state_qos,
        )

    def _create_timer(self) -> None:
        if self._node is None:
            return
        period_sec = max(self._sample_period_ms, 50) / 1000.0
        self._node.create_timer(period_sec, self._tick)

    def _on_state(self, msg: Any) -> None:
        self._latest["connected"] = bool(msg.connected)
        self._latest["armed"] = bool(msg.armed)
        self._latest["mode"] = str(msg.mode)

    def _on_odom(self, msg: Any) -> None:
        self._latest["position"] = {
            "x": float(msg.pose.pose.position.x),
            "y": float(msg.pose.pose.position.y),
            "z": float(msg.pose.pose.position.z),
        }

    def _on_velocity(self, msg: Any) -> None:
        self._latest["speed_mps"] = math.sqrt(
            float(msg.twist.linear.x) ** 2
            + float(msg.twist.linear.y) ** 2
            + float(msg.twist.linear.z) ** 2
        )

    def _on_extended_state(self, msg: Any) -> None:
        self._latest["on_ground"] = int(msg.landed_state) == 1

    def _tick(self) -> None:
        sample = ObserverSample(
            mono_ns=monotonic_ns(),
            wall_time=wall_time_iso(),
            connected=bool(self._latest["connected"]),
            armed=bool(self._latest["armed"]),
            mode=str(self._latest["mode"]),
            position=dict(self._latest["position"]),
            speed_mps=float(self._latest["speed_mps"]),
            on_ground=bool(self._latest["on_ground"]),
        )
        if self._started_mono_ns is None:
            self._started_mono_ns = sample.mono_ns
        events = self._tracker.process(sample)
        events.extend(self._process_external_fault_events(sample))
        for event in events:
            self._recorder.record(
                "observer_event",
                payload=event,
                metadata={"mission_id": self._mission},
                recorded_mono_ns=sample.mono_ns,
                recorded_wall_time=sample.wall_time,
            )
        if self._stop_when_complete and self._tracker.is_complete():
            self.request_stop()

    def _process_external_fault_events(self, sample: ObserverSample) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self._fault_events_jsonl is not None and self._fault_events_jsonl.exists():
            lines = self._fault_events_jsonl.read_text(encoding="utf-8").splitlines()
            new_lines = lines[self._fault_event_index :]
            self._fault_event_index = len(lines)
            for line in new_lines:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("status")) != "ok":
                    continue
                if self._tracker.summary()["touchdown_seen"]:
                    continue
                self._external_fault_active = True
                self._external_fault_count += 1
                if self._external_first_fault_mono_ns is None:
                    self._external_first_fault_mono_ns = sample.mono_ns
                events.append(
                    self._event(
                        sample,
                        "fault_detected",
                        fault_kind="external_injection",
                        fault_note=str(payload.get("note") or ""),
                    )
                )
        if self._external_fault_active and self._is_recovered(sample):
            self._external_fault_active = False
            self._external_recovered_fault_count += 1
            if self._external_first_recovery_mono_ns is None:
                self._external_first_recovery_mono_ns = sample.mono_ns
            events.append(
                self._event(
                    sample,
                    "fault_recovered",
                    recovered_faults=["external_injection"],
                )
            )
        return events

    def _is_recovered(self, sample: ObserverSample) -> bool:
        stable = sample.speed_mps <= self._tracker._STABLE_SPEED_MPS
        return (stable and sample.mode in {"AUTO.LOITER", "AUTO.RTL"}) or bool(
            self._tracker.summary()["touchdown_seen"]
        )

    def _elapsed_sec(self, target_mono_ns: int | None) -> float:
        if self._started_mono_ns is None or target_mono_ns is None:
            return 0.0
        return max(target_mono_ns - self._started_mono_ns, 0) / 1_000_000_000

    def _event(self, sample: ObserverSample, event_kind: str, **payload: Any) -> dict[str, Any]:
        return {
            "event_kind": event_kind,
            "mission": self._mission,
            "mode": sample.mode,
            "connected": sample.connected,
            "armed": sample.armed,
            "speed_mps": round(sample.speed_mps, 3),
            "position": {
                "x": round(float(sample.position.get("x", 0.0)), 3),
                "y": round(float(sample.position.get("y", 0.0)), 3),
                "z": round(float(sample.position.get("z", 0.0)), 3),
            },
            **payload,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="control-view-observer")
    parser.add_argument("--mission", required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--fault-events-jsonl", type=Path, default=None)
    parser.add_argument("--namespace", default="/mavros")
    parser.add_argument("--sample-period-ms", type=int, default=200)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--stop-when-complete", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    recorder = ReplayRecorder(
        default_metadata={"observer": True, "mission_id": args.mission},
        stream_path=args.output_jsonl,
    )
    node = ObserverNode(
        mission=args.mission,
        namespace=args.namespace,
        sample_period_ms=args.sample_period_ms,
        recorder=recorder,
        fault_events_jsonl=args.fault_events_jsonl,
        stop_when_complete=args.stop_when_complete,
    )

    def _handle_signal(_signum, _frame) -> None:
        node.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        node.run(duration_sec=args.duration_sec)
    finally:
        recorder.dump_jsonl(args.output_jsonl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
