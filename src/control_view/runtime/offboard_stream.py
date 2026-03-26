from __future__ import annotations

import threading
import time
from collections.abc import Callable

from control_view.common.time import monotonic_ns
from control_view.common.types import JSONDict


class OffboardStreamWorker:
    def __init__(
        self,
        publish_fn: Callable[[JSONDict], None] | None = None,
    ) -> None:
        self._publish_fn = publish_fn
        self._target_pose: JSONDict | None = None
        self._rate_hz: float = 20.0
        self._warmup_sec: float = 1.0
        self._started_mono_ns: int | None = None
        self._last_publish_mono_ns: int | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, target_pose: JSONDict, rate_hz: float, warmup_sec: float) -> None:
        self.stop()
        self._target_pose = target_pose
        self._rate_hz = rate_hz
        self._warmup_sec = warmup_sec
        self._started_mono_ns = monotonic_ns()
        self._last_publish_mono_ns = self._started_mono_ns
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def update_target(self, target_pose: JSONDict) -> None:
        self._target_pose = target_pose
        self.mark_publish()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None
        self._target_pose = None
        self._started_mono_ns = None
        self._last_publish_mono_ns = None

    def mark_publish(self) -> None:
        self._last_publish_mono_ns = monotonic_ns()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self._target_pose is not None and self._publish_fn is not None:
                self._publish_fn(self._target_pose)
                self.mark_publish()
            time.sleep(1.0 / max(self._rate_hz, 1.0))

    def snapshot_value(self) -> JSONDict:
        now_ns = monotonic_ns()
        started = self._started_mono_ns or now_ns
        last_publish = self._last_publish_mono_ns or now_ns
        warmup_elapsed_ms = (now_ns - started) / 1_000_000
        last_publish_age_ms = (now_ns - last_publish) / 1_000_000
        ok = (
            self._target_pose is not None
            and warmup_elapsed_ms >= (self._warmup_sec * 1000)
            and last_publish_age_ms <= 250
        )
        return {
            "value": ok,
            "publish_rate_hz": self._rate_hz,
            "last_publish_age_ms": round(last_publish_age_ms, 3),
            "warmup_elapsed_ms": round(warmup_elapsed_ms, 3),
        }
