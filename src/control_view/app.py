from __future__ import annotations

from pathlib import Path

from control_view.service import ControlViewService


def main() -> None:
    root = Path.cwd()
    service = ControlViewService(root)
    message = (
        f"Loaded {len(service.bundle.fields)} field specs and "
        f"{len(service.compiled)} family contracts."
    )
    print(message)


if __name__ == "__main__":
    main()
