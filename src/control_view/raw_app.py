from __future__ import annotations

import argparse
from pathlib import Path

from fastmcp import FastMCP

from control_view.app import _load_yaml
from control_view.backend.fake_backend import FakeBackend
from control_view.backend.mavros_backend import MavrosBackend
from control_view.mcp_server.raw_tools import register_raw_tools
from control_view.replay.recorder import ReplayRecorder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="control-view-raw-mcp")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--backend", choices=["mavros", "fake"], default="mavros")
    parser.add_argument(
        "--backend-config",
        type=Path,
        default=Path("configs/backend_mavros.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument("--record-jsonl", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    backend_config = args.backend_config
    if not backend_config.is_absolute():
        backend_config = root / backend_config
    backend_payload = _load_yaml(backend_config)
    artifact_dir = (args.artifact_dir or (root / "artifacts")).resolve()
    recorder = (
        ReplayRecorder(
            default_metadata={"raw_mcp": True},
            stream_path=args.record_jsonl,
        )
        if args.record_jsonl
        else None
    )
    if args.backend == "fake":
        backend = FakeBackend()
    else:
        backend = MavrosBackend(backend_payload)
    message = f"Loaded raw MCP server, backend={args.backend}, artifacts={artifact_dir}."
    if args.dry_run:
        if recorder is not None and args.record_jsonl is not None:
            recorder.dump_jsonl(args.record_jsonl)
        print(message)
        return 0

    server = FastMCP("control-view-raw-mcp")
    register_raw_tools(server, backend=backend, artifacts_dir=artifact_dir, recorder=recorder)
    print(message)
    try:
        server.run()
    finally:
        if recorder is not None and args.record_jsonl is not None:
            recorder.dump_jsonl(args.record_jsonl)
        if hasattr(backend, "shutdown"):
            backend.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
