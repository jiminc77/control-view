from __future__ import annotations

from control_view.runtime.lease import LeaseManager


def test_lease_signature_and_hash_are_stable() -> None:
    manager = LeaseManager("test-secret")
    token = manager.issue(
        "ARM",
        critical_slot_revisions={"vehicle.connected": 1},
        canonical_args={},
        issued_mono_ns=10,
        expires_mono_ns=100,
    )

    assert manager.verify_signature(token)
    assert manager.canonical_arg_hash({}) == token.arg_hash

