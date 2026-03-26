from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from control_view.backend.fake_backend import FakeBackend
from control_view.backend.mavros_backend import MavrosBackend
from control_view.mcp_server.server import build_server
from control_view.replay.recorder import ReplayRecorder
from control_view.service import ControlViewService


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text())
    return loaded or {}


def build_service(
    root: Path,
    *,
    backend_kind: str,
    backend_config_path: Path,
    sqlite_path: str | None = None,
    recorder: ReplayRecorder | None = None,
) -> ControlViewService:
    backend_config = _load_yaml(backend_config_path)
    if backend_kind == "fake":
        backend = FakeBackend()
    else:
        backend = MavrosBackend(backend_config)
    system_config = _load_yaml(root / "configs" / "system.yaml")
    sqlite_target = sqlite_path or system_config.get("system", {}).get("storage", {}).get(
        "sqlite_path",
        "control_view.sqlite3",
    )
    return ControlViewService(
        root,
        backend=backend,
        sqlite_path=sqlite_target,
        recorder=recorder,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="control-view-sidecar")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--backend", choices=["mavros", "fake"], default="mavros")
    parser.add_argument(
        "--backend-config",
        type=Path,
        default=Path("configs/backend_mavros.yaml"),
    )
    parser.add_argument("--sqlite-path", default=None)
    parser.add_argument("--record-jsonl", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    backend_config = args.backend_config
    if not backend_config.is_absolute():
        backend_config = root / backend_config
    recorder = ReplayRecorder() if args.record_jsonl else None
    service = build_service(
        root,
        backend_kind=args.backend,
        backend_config_path=backend_config,
        sqlite_path=args.sqlite_path,
        recorder=recorder,
    )
    message = (
        f"Loaded {len(service.bundle.fields)} field specs, "
        f"{len(service.compiled)} family contracts, backend={args.backend}."
    )
    if args.dry_run:
        if recorder is not None and args.record_jsonl is not None:
            recorder.dump_jsonl(args.record_jsonl)
        print(message)
        return 0
    print(message)
    try:
        build_server(service).run()
    finally:
        if recorder is not None and args.record_jsonl is not None:
            recorder.dump_jsonl(args.record_jsonl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
