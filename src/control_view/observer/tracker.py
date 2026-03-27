from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any


@dataclass(slots=True)
class ObserverSample:
    mono_ns: int
    wall_time: str
    connected: bool
    armed: bool
    mode: str
    position: dict[str, float]
    speed_mps: float
    on_ground: bool


class MissionObserverTracker:
    _MANUAL_MODES = {"ALTCTL", "MANUAL", "POSCTL", "STABILIZED"}
    _STABLE_SPEED_MPS = 0.3

    def __init__(self, mission: str) -> None:
        self.mission = mission
        self._spec = self._mission_spec(mission)
        self._started_mono_ns: int | None = None
        self._last_mono_ns: int | None = None
        self._reference_position: dict[str, float] | None = None
        self._airborne_since_ns: int | None = None
        self._arrival_mono_ns: int | None = None
        self._touchdown_mono_ns: int | None = None
        self._hold_mono_ns: int | None = None
        self._rtl_mono_ns: int | None = None
        self._first_fault_mono_ns: int | None = None
        self._first_recovery_mono_ns: int | None = None
        self._max_excursion_m = 0.0
        self._airborne_seen = False
        self._arrival_seen = False
        self._touchdown_seen = False
        self._hold_seen = False
        self._rtl_seen = False
        self._manual_override_needed = False
        self._fault_count = 0
        self._recovered_fault_count = 0
        self._active_faults: set[str] = set()
        self._last_connected: bool | None = None
        self._last_mode: str | None = None
        self._last_on_ground: bool | None = None

    def process(self, sample: ObserverSample) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self._started_mono_ns is None:
            self._started_mono_ns = sample.mono_ns
        self._last_mono_ns = sample.mono_ns
        if self._reference_position is None:
            self._reference_position = dict(sample.position)

        if self._last_connected is None or sample.connected != self._last_connected:
            events.append(
                self._event(
                    sample,
                    "connected_changed",
                    connected=sample.connected,
                )
            )
            if self._last_connected is True and not sample.connected:
                events.extend(self._open_fault(sample, "vehicle_disconnect"))
        self._last_connected = sample.connected

        if self._last_mode is None or sample.mode != self._last_mode:
            events.append(self._event(sample, "mode_changed", mode=sample.mode))
            if (
                self._last_mode == "OFFBOARD"
                and sample.mode in self._MANUAL_MODES
                and not sample.on_ground
            ):
                self._manual_override_needed = True
                events.extend(self._open_fault(sample, "operator_override"))
            elif (
                self._last_mode == "OFFBOARD"
                and sample.mode != "OFFBOARD"
                and not self._arrival_seen
                and not sample.on_ground
            ):
                events.extend(self._open_fault(sample, "offboard_lost_before_arrival"))
            elif (
                self._last_mode is not None
                and self._last_mode not in self._MANUAL_MODES
                and sample.mode in self._MANUAL_MODES
                and not sample.on_ground
            ):
                self._manual_override_needed = True
                events.extend(self._open_fault(sample, "operator_override"))
        self._last_mode = sample.mode

        airborne = (not sample.on_ground) and sample.armed
        if not self._airborne_seen and airborne:
            self._airborne_seen = True
            self._airborne_since_ns = sample.mono_ns
            self._reference_position = dict(sample.position)
            events.append(self._event(sample, "airborne"))

        excursion_m = self._horizontal_excursion(sample.position)
        if excursion_m > self._max_excursion_m:
            self._max_excursion_m = excursion_m
        if (
            self._spec["required_excursion_m"] > 0.0
            and self._max_excursion_m >= self._spec["required_excursion_m"]
            and not any(event["event_kind"] == "excursion_reached" for event in events)
            and not getattr(self, "_excursion_emitted", False)
        ):
            self._excursion_emitted = True
            events.append(
                self._event(
                    sample,
                    "excursion_reached",
                    excursion_m=round(self._max_excursion_m, 3),
                )
            )

        stable = sample.speed_mps <= self._STABLE_SPEED_MPS
        if sample.mode == "AUTO.LOITER" and stable and not self._hold_seen:
            self._hold_seen = True
            self._hold_mono_ns = sample.mono_ns
            events.append(self._event(sample, "hold_stable"))
        if sample.mode == "AUTO.RTL" and not self._rtl_seen:
            self._rtl_seen = True
            self._rtl_mono_ns = sample.mono_ns
            events.append(self._event(sample, "rtl_entered"))
        if (
            not self._arrival_seen
            and self._spec["required_excursion_m"] > 0.0
            and self._max_excursion_m >= self._spec["required_excursion_m"]
            and stable
            and sample.mode in {"AUTO.LOITER", "AUTO.RTL", "OFFBOARD"}
        ):
            self._arrival_seen = True
            self._arrival_mono_ns = sample.mono_ns
            events.append(
                self._event(
                    sample,
                    "arrival",
                    excursion_m=round(self._max_excursion_m, 3),
                )
            )

        if self._airborne_seen and sample.on_ground and not self._touchdown_seen:
            self._touchdown_seen = True
            self._touchdown_mono_ns = sample.mono_ns
            events.append(self._event(sample, "touchdown"))
        self._last_on_ground = sample.on_ground

        if self._active_faults and self._is_recovered(sample):
            recovered_faults = sorted(self._active_faults)
            self._recovered_fault_count += len(recovered_faults)
            self._active_faults.clear()
            if self._first_recovery_mono_ns is None:
                self._first_recovery_mono_ns = sample.mono_ns
            events.append(
                self._event(
                    sample,
                    "fault_recovered",
                    recovered_faults=recovered_faults,
                )
            )
        return events

    def is_complete(self) -> bool:
        return bool(self.summary()["mission_success"])

    def summary(self) -> dict[str, Any]:
        mission_success = self._mission_success()
        degraded_safe_outcome = bool(
            not mission_success and self._touchdown_seen and self._airborne_seen
        )
        return {
            "mission": self.mission,
            "mission_success": mission_success,
            "degraded_safe_outcome": degraded_safe_outcome,
            "manual_override_needed": self._manual_override_needed,
            "airborne_seen": self._airborne_seen,
            "arrival_seen": self._arrival_seen,
            "touchdown_seen": self._touchdown_seen,
            "hold_seen": self._hold_seen,
            "rtl_seen": self._rtl_seen,
            "fault_count": self._fault_count,
            "recovered_fault_count": self._recovered_fault_count,
            "max_excursion_m": round(self._max_excursion_m, 3),
            "observer_elapsed_sec": round(self._elapsed_sec(self._last_mono_ns), 3),
            "time_to_airborne_sec": round(self._elapsed_sec(self._airborne_since_ns), 3),
            "time_to_arrival_sec": round(self._elapsed_sec(self._arrival_mono_ns), 3),
            "time_to_touchdown_sec": round(self._elapsed_sec(self._touchdown_mono_ns), 3),
            "time_to_first_recovery_sec": round(
                self._elapsed_sec(self._first_recovery_mono_ns),
                3,
            ),
            "first_fault_mono_ns": self._first_fault_mono_ns,
        }

    def _mission_success(self) -> bool:
        if self.mission == "takeoff_hold_land":
            return self._airborne_seen and self._hold_seen and self._touchdown_seen
        if self.mission == "goto_hold_land":
            return (
                self._airborne_seen
                and self._arrival_seen
                and self._hold_seen
                and self._touchdown_seen
            )
        if self.mission == "goto_rtl":
            return (
                self._airborne_seen
                and self._arrival_seen
                and self._rtl_seen
                and self._touchdown_seen
            )
        return False

    def _mission_spec(self, mission: str) -> dict[str, Any]:
        specs = {
            "takeoff_hold_land": {"required_excursion_m": 0.0},
            "goto_hold_land": {"required_excursion_m": 1.5},
            "goto_rtl": {"required_excursion_m": 1.5},
        }
        if mission not in specs:
            raise ValueError(f"unsupported mission: {mission}")
        return specs[mission]

    def _horizontal_excursion(self, position: dict[str, float]) -> float:
        if self._reference_position is None:
            return 0.0
        return hypot(
            float(position.get("x", 0.0)) - float(self._reference_position.get("x", 0.0)),
            float(position.get("y", 0.0)) - float(self._reference_position.get("y", 0.0)),
        )

    def _elapsed_sec(self, target_mono_ns: int | None) -> float:
        if self._started_mono_ns is None or target_mono_ns is None:
            return 0.0
        return max(target_mono_ns - self._started_mono_ns, 0) / 1_000_000_000

    def _open_fault(self, sample: ObserverSample, kind: str) -> list[dict[str, Any]]:
        if kind in self._active_faults:
            return []
        self._active_faults.add(kind)
        self._fault_count += 1
        if self._first_fault_mono_ns is None:
            self._first_fault_mono_ns = sample.mono_ns
        return [self._event(sample, "fault_detected", fault_kind=kind)]

    def _is_recovered(self, sample: ObserverSample) -> bool:
        stable = sample.speed_mps <= self._STABLE_SPEED_MPS
        return stable and sample.mode in {"AUTO.LOITER", "AUTO.RTL"} or self._touchdown_seen

    def _event(self, sample: ObserverSample, event_kind: str, **payload: Any) -> dict[str, Any]:
        return {
            "event_kind": event_kind,
            "mission": self.mission,
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
