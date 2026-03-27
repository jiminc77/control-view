from __future__ import annotations

import argparse
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
        stop_when_complete: bool,
    ) -> None:
        self._mission = mission
        self._namespace = namespace
        self._sample_period_ms = sample_period_ms
        self._recorder = recorder
        self._tracker = MissionObserverTracker(mission)
        self._stop_when_complete = stop_when_complete
        self._stop_event = threading.Event()
        self._rclpy: Any | None = None
        self._node: Any | None = None
        self._executor: Any | None = None
        self._spin_thread: threading.Thread | None = None
        self._latest: dict[str, Any] = {
            "connected": False,
            "armed": False,
            "mode": "",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "speed_mps": 0.0,
            "on_ground": True,
        }

    def run(self, *, duration_sec: float | None = None) -> None:
        self._ensure_runtime()
        if self._node is None:
            return
        self._create_timer()
        deadline = None if duration_sec is None else monotonic_ns() + int(duration_sec * 1_000_000_000)
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
        events = self._tracker.process(sample)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="control-view-observer")
    parser.add_argument("--mission", required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--namespace", default="/mavros")
    parser.add_argument("--sample-period-ms", type=int, default=200)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--stop-when-complete", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    recorder = ReplayRecorder(default_metadata={"observer": True, "mission_id": args.mission})
    node = ObserverNode(
        mission=args.mission,
        namespace=args.namespace,
        sample_period_ms=args.sample_period_ms,
        recorder=recorder,
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
