from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

from control_view.common.utils import stable_json_dumps
from control_view.contracts.models import LeaseToken


class LeaseManager:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def canonical_arg_hash(self, canonical_args: dict) -> str:
        return hashlib.sha256(stable_json_dumps(canonical_args).encode("utf-8")).hexdigest()

    def issue(
        self,
        family: str,
        *,
        critical_slot_revisions: dict[str, int],
        canonical_args: dict,
        issued_mono_ns: int,
        expires_mono_ns: int,
    ) -> LeaseToken:
        payload = {
            "family": family,
            "critical_slot_revisions": critical_slot_revisions,
            "arg_hash": self.canonical_arg_hash(canonical_args),
            "issued_mono_ns": issued_mono_ns,
            "expires_mono_ns": expires_mono_ns,
            "nonce": str(uuid4()),
        }
        signature = self._sign(payload)
        return LeaseToken(
            lease_id=str(uuid4()),
            family=family,
            issued_mono_ns=issued_mono_ns,
            expires_mono_ns=expires_mono_ns,
            critical_slot_revisions=critical_slot_revisions,
            arg_hash=payload["arg_hash"],
            nonce=payload["nonce"],
            signature=signature,
        )

    def verify_signature(self, token: LeaseToken) -> bool:
        payload = {
            "family": token.family,
            "critical_slot_revisions": token.critical_slot_revisions,
            "arg_hash": token.arg_hash,
            "issued_mono_ns": token.issued_mono_ns,
            "expires_mono_ns": token.expires_mono_ns,
            "nonce": token.nonce,
        }
        return hmac.compare_digest(token.signature, self._sign(payload))

    def _sign(self, payload: dict) -> str:
        return hmac.new(
            self._secret,
            stable_json_dumps(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

