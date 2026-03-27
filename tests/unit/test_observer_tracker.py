from __future__ import annotations

from control_view.observer import MissionObserverTracker, ObserverSample


def _sample(
    mono_ns: int,
    *,
    mode: str,
    armed: bool,
    on_ground: bool,
    x: float,
    y: float,
    z: float,
    speed_mps: float,
    connected: bool = True,
) -> ObserverSample:
    return ObserverSample(
        mono_ns=mono_ns,
        wall_time="2026-03-27T00:00:00Z",
        connected=connected,
        armed=armed,
        mode=mode,
        position={"x": x, "y": y, "z": z},
        speed_mps=speed_mps,
        on_ground=on_ground,
    )


def test_takeoff_hold_land_summary_marks_success() -> None:
    tracker = MissionObserverTracker("takeoff_hold_land")
    tracker.process(
        _sample(0, mode="POSCTL", armed=False, on_ground=True, x=0.0, y=0.0, z=0.0, speed_mps=0.0)
    )
    tracker.process(
        _sample(
            1_000_000_000,
            mode="AUTO.TAKEOFF",
            armed=True,
            on_ground=False,
            x=0.0,
            y=0.0,
            z=2.8,
            speed_mps=0.8,
        )
    )
    tracker.process(
        _sample(
            2_000_000_000,
            mode="AUTO.LOITER",
            armed=True,
            on_ground=False,
            x=0.0,
            y=0.0,
            z=3.0,
            speed_mps=0.1,
        )
    )
    tracker.process(
        _sample(
            3_000_000_000,
            mode="AUTO.LAND",
            armed=False,
            on_ground=True,
            x=0.0,
            y=0.0,
            z=0.0,
            speed_mps=0.0,
        )
    )

    summary = tracker.summary()

    assert summary["mission_success"] is True
    assert summary["airborne_seen"] is True
    assert summary["hold_seen"] is True
    assert summary["touchdown_seen"] is True


def test_goto_hold_land_tracks_arrival_and_excursion() -> None:
    tracker = MissionObserverTracker("goto_hold_land")
    tracker.process(
        _sample(0, mode="POSCTL", armed=False, on_ground=True, x=0.0, y=0.0, z=0.0, speed_mps=0.0)
    )
    tracker.process(
        _sample(
            1_000_000_000,
            mode="AUTO.TAKEOFF",
            armed=True,
            on_ground=False,
            x=0.0,
            y=0.0,
            z=2.8,
            speed_mps=0.9,
        )
    )
    tracker.process(
        _sample(
            2_000_000_000,
            mode="OFFBOARD",
            armed=True,
            on_ground=False,
            x=2.0,
            y=0.0,
            z=3.0,
            speed_mps=0.2,
        )
    )
    tracker.process(
        _sample(
            3_000_000_000,
            mode="AUTO.LOITER",
            armed=True,
            on_ground=False,
            x=2.0,
            y=0.0,
            z=3.0,
            speed_mps=0.1,
        )
    )
    tracker.process(
        _sample(
            4_000_000_000,
            mode="AUTO.LAND",
            armed=False,
            on_ground=True,
            x=2.0,
            y=0.0,
            z=0.0,
            speed_mps=0.0,
        )
    )

    summary = tracker.summary()

    assert summary["mission_success"] is True
    assert summary["arrival_seen"] is True
    assert summary["max_excursion_m"] >= 1.5


def test_tracker_detects_fault_and_recovery() -> None:
    tracker = MissionObserverTracker("goto_rtl")
    tracker.process(
        _sample(0, mode="AUTO.LOITER", armed=True, on_ground=False, x=0.0, y=0.0, z=3.0, speed_mps=0.0)
    )
    tracker.process(
        _sample(
            1_000_000_000,
            mode="OFFBOARD",
            armed=True,
            on_ground=False,
            x=1.0,
            y=0.0,
            z=3.0,
            speed_mps=0.8,
        )
    )
    fault_events = tracker.process(
        _sample(
            2_000_000_000,
            mode="POSCTL",
            armed=True,
            on_ground=False,
            x=1.2,
            y=0.0,
            z=3.0,
            speed_mps=0.6,
        )
    )
    recovery_events = tracker.process(
        _sample(
            3_000_000_000,
            mode="AUTO.LOITER",
            armed=True,
            on_ground=False,
            x=1.2,
            y=0.0,
            z=3.0,
            speed_mps=0.1,
        )
    )

    assert any(event["event_kind"] == "fault_detected" for event in fault_events)
    assert any(event["event_kind"] == "fault_recovered" for event in recovery_events)
    assert tracker.summary()["fault_count"] == 1
    assert tracker.summary()["recovered_fault_count"] == 1
