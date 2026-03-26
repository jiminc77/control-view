from __future__ import annotations

from pathlib import Path

from control_view.contracts.compiler import compile_bundle
from control_view.contracts.loader import load_contract_bundle


def main() -> None:
    root = Path.cwd()
    bundle = load_contract_bundle(root)
    compiled = compile_bundle(bundle)
    print(f"Loaded {len(bundle.fields)} field specs and {len(compiled)} family contracts.")


if __name__ == "__main__":
    main()

