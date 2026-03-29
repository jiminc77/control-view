from __future__ import annotations

import atexit
import math
import threading
import time
from typing import Any

from control_view.backend.base import BackendActionResult, BackendAdapter, BackendSlotValue
from control_view.common.time import monotonic_ns
from control_view.common.types import ActionState, JSONDict
from control_view.common.utils import distance_3d, quaternion_to_yaw
from control_view.runtime.offboard_stream import OffboardStreamWorker


class MavrosBackend(BackendAdapter):
    def __init__(self, config: JSONDict | None = None) -> None:
        self.config = (config or {}).get("backend", config or {})
        self._snapshot_cache: dict[str, BackendSlotValue] = {}
        self._lock = threading.RLock()
        self._odom_pose: BackendSlotValue | None = None
        self._fallback_pose: BackendSlotValue | None = None
        self._rclpy: Any | None = None
        self._node: Any | None = None
        self._executor: Any | None = None
        self._spin_thread: threading.Thread | None = None
        self._publisher: Any | None = None
        self._clients: dict[str, Any] = {}
        self._global_fix: JSONDict | None = None
        self._current_yaw: float | None = None
        self._extended_state: dict[str, Any] = {"landed_state": None}
        self._ground_reference_z: float | None = None
        self._active_target_pose: JSONDict | None = None
        self._preview_target_pose: JSONDict | None = None
        self._active_takeoff: JSONDict | None = None
        self._battery_reserve_fraction = 0.20
        self._offboard_worker = OffboardStreamWorker(self._publish_setpoint)
        self._subscriptions_ready = False
        atexit.register(self.shutdown)

    def _require_ros(self) -> Any:
        try:
            import rclpy  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "rclpy is required for MavrosBackend at runtime on Ubuntu 24.04 / ROS 2 Jazzy"
            ) from exc
        return rclpy

    def _ensure_runtime(self) -> None:
        if self._node is not None:
            return
        rclpy = self._require_ros()
        self._rclpy = rclpy
        if not rclpy.ok():
            rclpy.init(args=None)
        from geometry_msgs.msg import PoseStamped
        from rclpy.executors import SingleThreadedExecutor

        namespace = str(self.config.get("namespace", "/mavros"))
        node_name = str(self.config.get("node_name", "control_view_sidecar"))
        self._node = rclpy.create_node(node_name, namespace="")
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._publisher = self._node.create_publisher(
            PoseStamped,
            str(self.config.get("setpoint_topic", f"{namespace}/setpoint_position/local")),
            10,
        )
        self._create_subscriptions(namespace)
        self._create_clients(namespace)
        self._spin_thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._spin_thread.start()

    def _create_subscriptions(self, namespace: str) -> None:
        if self._subscriptions_ready:
            return
        from geometry_msgs.msg import PoseStamped, TwistStamped
        from mavros_msgs.msg import EstimatorStatus, ExtendedState, HomePosition, State, StatusText
        from nav_msgs.msg import Odometry
        from rclpy.qos import (
            DurabilityPolicy,
            HistoryPolicy,
            QoSProfile,
            ReliabilityPolicy,
            qos_profile_sensor_data,
        )
        from sensor_msgs.msg import BatteryState, NavSatFix

        state_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        sensor_qos = qos_profile_sensor_data

        self._node.create_subscription(State, f"{namespace}/state", self._on_state, state_qos)
        self._node.create_subscription(
            Odometry,
            f"{namespace}/local_position/odom",
            self._on_odom,
            sensor_qos,
        )
        self._node.create_subscription(
            PoseStamped,
            f"{namespace}/local_position/pose",
            self._on_pose_fallback,
            sensor_qos,
        )
        self._node.create_subscription(
            TwistStamped,
            f"{namespace}/local_position/velocity_local",
            self._on_velocity,
            sensor_qos,
        )
        self._node.create_subscription(
            EstimatorStatus,
            f"{namespace}/estimator_status",
            self._on_estimator_status,
            sensor_qos,
        )
        self._node.create_subscription(
            StatusText,
            f"{namespace}/statustext/recv",
            self._on_status_text,
            sensor_qos,
        )
        self._node.create_subscription(
            HomePosition,
            f"{namespace}/home_position/home",
            self._on_home_position,
            state_qos,
        )
        self._node.create_subscription(
            NavSatFix,
            f"{namespace}/global_position/global",
            self._on_global_fix,
            sensor_qos,
        )
        self._node.create_subscription(
            BatteryState,
            f"{namespace}/battery",
            self._on_battery_state,
            sensor_qos,
        )
        self._node.create_subscription(
            ExtendedState,
            f"{namespace}/extended_state",
            self._on_extended_state,
            state_qos,
        )
        self._subscriptions_ready = True

    def _create_clients(self, namespace: str) -> None:
        from mavros_msgs.srv import CommandBool, CommandTOL, SetMode

        self._clients = {
            "arm": self._node.create_client(
                CommandBool,
                str(self.config.get("arm_service", f"{namespace}/cmd/arming")),
            ),
            "takeoff": self._node.create_client(
                CommandTOL,
                str(self.config.get("takeoff_service", f"{namespace}/cmd/takeoff")),
            ),
            "set_mode": self._node.create_client(
                SetMode,
                str(self.config.get("set_mode_service", f"{namespace}/set_mode")),
            ),
        }

    def _stamp_to_str(self, stamp: Any) -> str:
        return f"{int(stamp.sec)}.{int(stamp.nanosec):09d}"

    def _cache_slot(
        self,
        slot_id: str,
        value: Any,
        *,
        authority_source: str,
        source_header_stamp: str | None = None,
        frame_id: str | None = None,
        reason_codes: list[str] | None = None,
    ) -> None:
        with self._lock:
            self._snapshot_cache[slot_id] = BackendSlotValue(
                value=value,
                authority_source=authority_source,
                source_header_stamp=source_header_stamp,
                frame_id=frame_id,
                reason_codes=reason_codes or [],
            )

    def _on_state(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        self._cache_slot(
            "vehicle.connected",
            bool(msg.connected),
            authority_source="mavros/state",
            source_header_stamp=stamp,
        )
        self._cache_slot(
            "vehicle.armed",
            bool(msg.armed),
            authority_source="mavros/state",
            source_header_stamp=stamp,
        )
        self._cache_slot(
            "vehicle.mode",
            str(msg.mode),
            authority_source="mavros/state",
            source_header_stamp=stamp,
        )
        with self._lock:
            has_failsafe = "failsafe.state" in self._snapshot_cache
        if not has_failsafe:
            self._cache_slot(
                "failsafe.state",
                {
                    "active": False,
                    "source": "mavros_statustext_default",
                    "text": "",
                },
                authority_source="mavros/statustext_default",
                source_header_stamp=stamp,
            )

    def _pose_payload(
        self,
        position: Any,
        orientation: Any,
        *,
        frame_id: str,
        child_frame_id: str,
    ) -> JSONDict:
        return {
            "position": {
                "x": round(float(position.x), 3),
                "y": round(float(position.y), 3),
                "z": round(float(position.z), 3),
            },
            "orientation": {
                "x": float(orientation.x),
                "y": float(orientation.y),
                "z": float(orientation.z),
                "w": float(orientation.w),
            },
            "frame_id": frame_id,
            "child_frame_id": child_frame_id,
        }

    def _yaw_delta(self, yaw_a: float, yaw_b: float) -> float:
        delta = (yaw_a - yaw_b + math.pi) % (2.0 * math.pi) - math.pi
        return abs(delta)

    def _pose_disagreement_reason_codes(
        self,
        primary: BackendSlotValue | None,
        fallback: BackendSlotValue | None,
    ) -> list[str]:
        if primary is None or fallback is None:
            return []
        if primary.frame_id != fallback.frame_id:
            return ["source_disagreement:frame"]
        distance_tolerance = float(
            self.config.get("pose_disagreement", {}).get("position_tolerance_m", 0.75)
        )
        yaw_tolerance = float(
            self.config.get("pose_disagreement", {}).get("yaw_tolerance_rad", 0.6)
        )
        primary_position = primary.value.get("position")
        fallback_position = fallback.value.get("position")
        distance_m = distance_3d(primary_position, fallback_position) or 0.0
        primary_yaw = quaternion_to_yaw(primary.value.get("orientation"))
        fallback_yaw = quaternion_to_yaw(fallback.value.get("orientation"))
        if distance_m > distance_tolerance:
            return [f"source_disagreement:position:{round(distance_m, 3)}"]
        if self._yaw_delta(primary_yaw, fallback_yaw) > yaw_tolerance:
            return ["source_disagreement:yaw"]
        return []

    def _refresh_pose_slot(self) -> None:
        with self._lock:
            primary = self._odom_pose or self._fallback_pose
            if primary is None:
                return
            disagreement_codes = self._pose_disagreement_reason_codes(
                self._odom_pose,
                self._fallback_pose,
            )
            self._snapshot_cache["pose.local"] = BackendSlotValue(
                value=primary.value,
                authority_source=primary.authority_source,
                source_header_stamp=primary.source_header_stamp,
                frame_id=primary.frame_id,
                reason_codes=[*primary.reason_codes, *disagreement_codes],
            )
            self._current_yaw = quaternion_to_yaw(primary.value.get("orientation"))

    def _on_odom(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        payload = self._pose_payload(
            msg.pose.pose.position,
            msg.pose.pose.orientation,
            frame_id=msg.header.frame_id,
            child_frame_id=msg.child_frame_id,
        )
        payload["covariance"] = list(msg.pose.covariance)
        with self._lock:
            self._odom_pose = BackendSlotValue(
                value=payload,
                authority_source="mavros/odom",
                source_header_stamp=stamp,
                frame_id=msg.header.frame_id,
            )
        self._refresh_pose_slot()

    def _on_pose_fallback(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        payload = self._pose_payload(
            msg.pose.position,
            msg.pose.orientation,
            frame_id=msg.header.frame_id,
            child_frame_id="base_link",
        )
        with self._lock:
            self._fallback_pose = BackendSlotValue(
                value=payload,
                authority_source="mavros/pose_fallback",
                source_header_stamp=stamp,
                frame_id=msg.header.frame_id,
            )
        self._refresh_pose_slot()

    def _on_velocity(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        payload = {
            "linear": {
                "x": round(float(msg.twist.linear.x), 3),
                "y": round(float(msg.twist.linear.y), 3),
                "z": round(float(msg.twist.linear.z), 3),
            },
            "angular": {
                "x": round(float(msg.twist.angular.x), 3),
                "y": round(float(msg.twist.angular.y), 3),
                "z": round(float(msg.twist.angular.z), 3),
            },
            "frame_id": msg.header.frame_id,
        }
        self._cache_slot(
            "velocity.local",
            payload,
            authority_source="mavros/velocity_local",
            source_header_stamp=stamp,
            frame_id=msg.header.frame_id,
        )

    def _on_estimator_status(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        positive_flags = {
            "attitude": bool(msg.attitude_status_flag),
            "velocity_horiz": bool(msg.velocity_horiz_status_flag),
            "velocity_vert": bool(msg.velocity_vert_status_flag),
            "pos_horiz_rel": bool(msg.pos_horiz_rel_status_flag),
            "pos_horiz_abs": bool(msg.pos_horiz_abs_status_flag),
            "pos_vert_abs": bool(msg.pos_vert_abs_status_flag),
            "pos_vert_agl": bool(msg.pos_vert_agl_status_flag),
            "const_pos_mode": bool(msg.const_pos_mode_status_flag),
            "pred_pos_horiz_rel": bool(msg.pred_pos_horiz_rel_status_flag),
            "pred_pos_horiz_abs": bool(msg.pred_pos_horiz_abs_status_flag),
        }
        veto_flags = [name for name, ok in positive_flags.items() if not ok]
        if bool(msg.gps_glitch_status_flag):
            veto_flags.append("gps_glitch")
        if bool(msg.accel_error_status_flag):
            veto_flags.append("accel_error")
        positives = sum(1 for ok in positive_flags.values() if ok)
        score = positives / max(len(positive_flags), 1)
        if bool(msg.gps_glitch_status_flag):
            score -= 0.25
        if bool(msg.accel_error_status_flag):
            score -= 0.25
        self._cache_slot(
            "estimator.health",
            {
                "score": round(max(score, 0.0), 3),
                "veto_flags": veto_flags,
            },
            authority_source="mavros/estimator_status",
            source_header_stamp=stamp,
        )

    def _on_status_text(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        text = str(msg.text)
        lowered = text.lower()
        active = any(
            token in lowered
            for token in ["failsafe", "critical", "emergency", "gps", "ekf", "estimator"]
        )
        self._cache_slot(
            "failsafe.state",
            {
                "active": active,
                "source": "mavros_statustext",
                "text": text,
            },
            authority_source="mavros/statustext",
            source_header_stamp=stamp,
            reason_codes=["heuristic_failsafe_active"] if active else [],
        )

    def _on_home_position(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        self._cache_slot(
            "home.position",
            {
                "geo": {
                    "latitude": float(msg.geo.latitude),
                    "longitude": float(msg.geo.longitude),
                    "altitude": float(msg.geo.altitude),
                },
                "position": {
                    "x": round(float(msg.position.x), 3),
                    "y": round(float(msg.position.y), 3),
                    "z": round(float(msg.position.z), 3),
                },
                "frame_id": msg.header.frame_id,
            },
            authority_source="mavros/home_position",
            source_header_stamp=stamp,
            frame_id=msg.header.frame_id,
        )

    def _on_global_fix(self, msg: Any) -> None:
        self._global_fix = {
            "latitude": float(msg.latitude),
            "longitude": float(msg.longitude),
            "altitude": float(msg.altitude),
        }

    def _on_battery_state(self, msg: Any) -> None:
        stamp = self._stamp_to_str(msg.header.stamp)
        percentage = float(msg.percentage) if msg.percentage >= 0.0 else 0.0
        self._cache_slot(
            "battery.margin",
            {
                "margin_fraction": round(max(percentage - self._battery_reserve_fraction, 0.0), 3),
                "reserve_fraction": self._battery_reserve_fraction,
            },
            authority_source="mavros/battery",
            source_header_stamp=stamp,
        )

    def _on_extended_state(self, msg: Any) -> None:
        self._extended_state = {"landed_state": int(msg.landed_state)}

    def _publish_setpoint(self, target_pose: JSONDict) -> None:
        self._ensure_runtime()
        from geometry_msgs.msg import PoseStamped

        if self._publisher is None or self._node is None:
            return
        message = PoseStamped()
        message.header.stamp = self._node.get_clock().now().to_msg()
        message.header.frame_id = str(target_pose.get("frame_id", "map"))
        message.pose.position.x = float(target_pose.get("position", {}).get("x", 0.0))
        message.pose.position.y = float(target_pose.get("position", {}).get("y", 0.0))
        message.pose.position.z = float(target_pose.get("position", {}).get("z", 0.0))
        yaw = float(target_pose.get("yaw", 0.0))
        message.pose.orientation.x = 0.0
        message.pose.orientation.y = 0.0
        message.pose.orientation.z = math.sin(yaw / 2.0)
        message.pose.orientation.w = math.cos(yaw / 2.0)
        self._publisher.publish(message)

    def update_cached_slot(self, slot_id: str, value: BackendSlotValue) -> None:
        with self._lock:
            self._snapshot_cache[slot_id] = value

    def _offboard_params(
        self,
        canonical_args: JSONDict | None,
    ) -> tuple[JSONDict | None, float, float]:
        args = canonical_args or {}
        target_pose = args.get("target_pose")
        offboard_config = self.config.get("offboard", {})
        default_rate_hz = float(offboard_config.get("stream_rate_hz", 20.0))
        warmup_sec = float(offboard_config.get("warmup_sec", 1.0))
        if not isinstance(target_pose, dict):
            return None, default_rate_hz, warmup_sec
        rate_hz = float(args.get("stream_rate_hz", default_rate_hz))
        return target_pose, rate_hz, warmup_sec

    def _landing_touchdown_params(self) -> tuple[float, float]:
        landing_config = self.config.get("landing", {})
        altitude_m = float(landing_config.get("touchdown_altitude_m", 0.35))
        speed_mps = float(landing_config.get("touchdown_speed_mps", 0.2))
        return altitude_m, speed_mps

    def _ensure_offboard_stream(
        self,
        target_pose: JSONDict,
        *,
        rate_hz: float,
        warmup_sec: float,
    ) -> bool:
        snapshot = self._offboard_worker.snapshot_value()
        same_target = (
            self._preview_target_pose == target_pose
            or self._active_target_pose == target_pose
        )
        if not same_target or not snapshot.get("value", False):
            self._offboard_worker.start(target_pose, rate_hz=rate_hz, warmup_sec=warmup_sec)
        deadline = time.monotonic() + max(self._timeout("goto_sec", 3.0), warmup_sec + 0.5)
        while time.monotonic() < deadline:
            if self._offboard_worker.snapshot_value().get("value", False):
                return True
            time.sleep(0.05)
        return False

    def prepare_control_view(
        self,
        family: str,
        canonical_args: JSONDict | None = None,
    ) -> None:
        self._ensure_runtime()
        if family != "GOTO":
            if self._preview_target_pose is not None and self._active_target_pose is None:
                self._preview_target_pose = None
                self._offboard_worker.stop()
            return
        target_pose, rate_hz, warmup_sec = self._offboard_params(canonical_args)
        if target_pose is None:
            if self._preview_target_pose is not None and self._active_target_pose is None:
                self._preview_target_pose = None
                self._offboard_worker.stop()
            return
        self._preview_target_pose = target_pose
        if not self._ensure_offboard_stream(target_pose, rate_hz=rate_hz, warmup_sec=warmup_sec):
            self._preview_target_pose = None
            if self._active_target_pose is None:
                self._offboard_worker.stop()

    def get_current_snapshot(self, slot_ids: list[str]) -> dict[str, BackendSlotValue | None]:
        self._ensure_runtime()
        self._wait_for_slots(
            slot_ids,
            timeout_sec=float(self.config.get("startup_wait_sec", 2.0)),
        )
        return {slot_id: self.refresh_slot(slot_id) for slot_id in slot_ids}

    def refresh_slot(self, slot_id: str) -> BackendSlotValue | None:
        self._ensure_runtime()
        if slot_id == "offboard.stream.ok":
            snapshot = self._offboard_worker.snapshot_value()
            return BackendSlotValue(
                value=snapshot,
                authority_source="offboard_stream_worker",
                reason_codes=["offboard_stream_lost"] if not snapshot.get("value", False) else [],
            )
        if slot_id == "failsafe.state":
            with self._lock:
                cached = self._snapshot_cache.get(slot_id)
            if cached is not None:
                return cached
            return BackendSlotValue(
                value={
                    "active": False,
                    "source": "mavros_statustext_default",
                    "text": "",
                },
                authority_source="mavros/statustext_default",
            )
        with self._lock:
            return self._snapshot_cache.get(slot_id)

    def get_global_fix(self) -> JSONDict | None:
        self._ensure_runtime()
        return self._global_fix

    def get_current_yaw(self) -> float | None:
        self._ensure_runtime()
        return self._current_yaw

    def get_runtime_context(self) -> JSONDict:
        self._ensure_runtime()
        with self._lock:
            pose = self._snapshot_cache.get("pose.local")
            velocity = self._snapshot_cache.get("velocity.local")
            mode = self._snapshot_cache.get("vehicle.mode")
            armed = self._snapshot_cache.get("vehicle.armed")
            home = self._snapshot_cache.get("home.position")
        current_z = float((pose.value.get("position") or {}).get("z", 0.0)) if pose else 0.0
        speed_mps = 0.0
        if velocity:
            linear = velocity.value.get("linear", velocity.value)
            speed_mps = (
                float(linear.get("x", 0.0)) ** 2
                + float(linear.get("y", 0.0)) ** 2
                + float(linear.get("z", 0.0)) ** 2
            ) ** 0.5
        distance_m = distance_3d(
            (pose.value.get("position") if pose else None),
            (self._active_target_pose or {}).get("position"),
        )
        current_mode = str(mode.value) if mode else ""
        touchdown_altitude_m, touchdown_speed_mps = self._landing_touchdown_params()
        ground_reference_z = self._ground_reference_z
        if ground_reference_z is None and self._active_takeoff is not None:
            ground_reference_z = float(self._active_takeoff.get("initial_z", current_z))
        if ground_reference_z is None and home is not None:
            ground_reference_z = float((home.value.get("position") or {}).get("z", current_z))
        if ground_reference_z is None:
            ground_reference_z = current_z
        touchdown_distance_m = min(abs(current_z), abs(current_z - ground_reference_z))
        on_ground = self._extended_state.get("landed_state") == 1 or (
            touchdown_distance_m <= touchdown_altitude_m and speed_mps <= touchdown_speed_mps
        )
        arrived = distance_m is not None and distance_m <= 0.5 and speed_mps <= 0.3
        altitude_reached = bool(
            self._active_takeoff
            and current_z >= float(self._active_takeoff.get("target_z", 0.0)) - 0.3
        )
        altitude_gain_too_small = bool(
            self._active_takeoff
            and (
                monotonic_ns() - int(self._active_takeoff.get("issued_mono_ns", 0))
            ) > 5_000_000_000
            and current_z < float(self._active_takeoff.get("initial_z", 0.0)) + 0.5
        )
        return {
            "takeoff": {
                "airborne": not on_ground and bool(armed and armed.value),
                "altitude_reached": altitude_reached,
            },
            "land": {
                "on_ground": on_ground,
            },
            "goto": {
                "active_target_pose": self._active_target_pose,
                "distance_m": round(distance_m, 3) if distance_m is not None else None,
                "arrived": arrived,
            },
            "signals": {
                "OFFBOARD_lost_before_arrival": bool(
                    self._active_target_pose and current_mode != "OFFBOARD" and not arrived
                ),
                "altitude_gain_too_small": altitude_gain_too_small,
            },
        }

    def _wait_for_service(self, client: Any, timeout_sec: float) -> bool:
        return bool(client.wait_for_service(timeout_sec=timeout_sec))

    def _call_service(self, client_name: str, request: Any, timeout_sec: float) -> Any:
        self._ensure_runtime()
        client = self._clients[client_name]
        if not self._wait_for_service(client, timeout_sec):
            raise RuntimeError(f"{client_name} service unavailable")
        future = client.call_async(request)
        deadline = time.monotonic() + timeout_sec
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not future.done():
            raise TimeoutError(f"{client_name} service timed out")
        return future.result()

    def _timeout(self, key: str, default: float = 2.0) -> float:
        timeouts = self.config.get("timeouts", {})
        return float(timeouts.get(key, timeouts.get("default_sec", default)))

    def _wait_for_slots(self, slot_ids: list[str], timeout_sec: float) -> None:
        if timeout_sec <= 0.0:
            return
        wait_for = {
            slot_id
            for slot_id in slot_ids
            if slot_id
            not in {
                "offboard.stream.ok",
                "geofence.status",
                "nav.progress",
                "tool_registry.rev",
                "mission.spec.rev",
            }
        }
        if not wait_for:
            return
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            with self._lock:
                ready = wait_for.issubset(self._snapshot_cache)
            if ready:
                return
            time.sleep(0.05)

    def set_mode(self, mode: str) -> BackendActionResult:
        from mavros_msgs.srv import SetMode

        try:
            request = SetMode.Request()
            request.base_mode = 0
            request.custom_mode = mode
            response = self._call_service("set_mode", request, self._timeout("default_sec", 2.0))
        except Exception as exc:
            return BackendActionResult(
                state=ActionState.FAILED,
                response={"mode": mode},
                reason_codes=[f"set_mode_failed:{type(exc).__name__}"],
            )
        state = ActionState.ACKED_WEAK if bool(response.mode_sent) else ActionState.FAILED
        if state != ActionState.ACKED_WEAK:
            self._offboard_worker.stop()
        return BackendActionResult(
            state=state,
            response={"mode_sent": bool(response.mode_sent), "mode": mode},
        )

    def arm(self) -> BackendActionResult:
        from mavros_msgs.srv import CommandBool

        try:
            request = CommandBool.Request()
            request.value = True
            pose = self.refresh_slot("pose.local")
            if pose is not None:
                self._ground_reference_z = float((pose.value.get("position") or {}).get("z", 0.0))
            response = self._call_service("arm", request, self._timeout("default_sec", 2.0))
        except Exception as exc:
            return BackendActionResult(
                state=ActionState.FAILED,
                response={"action": "arm"},
                reason_codes=[f"arm_failed:{type(exc).__name__}"],
            )
        return BackendActionResult(
            state=ActionState.ACKED_STRONG if bool(response.success) else ActionState.FAILED,
            response={"success": bool(response.success), "result": int(response.result)},
        )

    def takeoff(self, target_altitude: float, geo_reference: JSONDict) -> BackendActionResult:
        from mavros_msgs.srv import CommandTOL

        pose = self.refresh_slot("pose.local")
        initial_z = float((pose.value.get("position") or {}).get("z", 0.0)) if pose else 0.0
        self._ground_reference_z = initial_z
        self._active_takeoff = {
            "issued_mono_ns": monotonic_ns(),
            "initial_z": initial_z,
            "target_z": initial_z + float(target_altitude),
        }
        try:
            request = CommandTOL.Request()
            request.min_pitch = 0.0
            request.yaw = float(self._current_yaw or 0.0)
            request.latitude = float(geo_reference["latitude"])
            request.longitude = float(geo_reference["longitude"])
            request.altitude = float(target_altitude)
            response = self._call_service("takeoff", request, self._timeout("takeoff_sec", 5.0))
        except Exception as exc:
            return BackendActionResult(
                state=ActionState.FAILED,
                response={"target_altitude": target_altitude},
                reason_codes=[f"takeoff_failed:{type(exc).__name__}"],
            )
        return BackendActionResult(
            state=ActionState.ACKED_STRONG if bool(response.success) else ActionState.FAILED,
            response={"success": bool(response.success), "result": int(response.result)},
        )

    def goto(self, target_pose: JSONDict, canonical_args: JSONDict) -> BackendActionResult:
        _, rate_hz, warmup_sec = self._offboard_params(canonical_args)
        self._active_target_pose = target_pose
        self._preview_target_pose = None
        if not self._ensure_offboard_stream(target_pose, rate_hz=rate_hz, warmup_sec=warmup_sec):
            self._offboard_worker.stop()
            self._active_target_pose = None
            return BackendActionResult(
                state=ActionState.FAILED,
                response={"target_pose": target_pose},
                reason_codes=["offboard_warmup_failed"],
            )
        result = self.set_mode("OFFBOARD")
        if result.state != ActionState.ACKED_WEAK:
            self._active_target_pose = None
        return result

    def hold(self) -> BackendActionResult:
        self._active_target_pose = None
        self._preview_target_pose = None
        self._offboard_worker.stop()
        return self.set_mode("AUTO.LOITER")

    def rtl(self) -> BackendActionResult:
        self._active_target_pose = None
        self._preview_target_pose = None
        self._offboard_worker.stop()
        return self.set_mode("AUTO.RTL")

    def land(self) -> BackendActionResult:
        self._active_target_pose = None
        self._preview_target_pose = None
        self._offboard_worker.stop()
        return self.set_mode("AUTO.LAND")

    def shutdown(self) -> None:
        self._preview_target_pose = None
        self._offboard_worker.stop()
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
