from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from control_view.contracts.models import LeaseToken
from control_view.service import ControlViewService


def register_tools(server: FastMCP, service: ControlViewService) -> None:
    @server.tool(name="control_view.get")
    def control_view_get(
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.get_control_view(family, proposed_args or {}).model_dump(mode="json")

    @server.tool(name="control_view.refresh")
    def control_view_refresh(
        family: str | None = None,
        slots: list[str] | None = None,
        proposed_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.refresh_control_view(
            family=family,
            slots=slots or [],
            proposed_args=proposed_args or {},
        ).model_dump(mode="json")

    @server.tool(name="action.execute_guarded")
    def action_execute_guarded(
        family: str,
        canonical_args: dict[str, Any],
        lease_token: dict[str, Any],
    ) -> dict[str, Any]:
        return service.execute_guarded(
            family,
            canonical_args,
            LeaseToken.model_validate(lease_token),
        ).model_dump(mode="json")

    @server.tool(name="control.explain_blockers")
    def control_explain_blockers(
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.explain_blockers(family, proposed_args or {})

    @server.tool(name="ledger.tail")
    def ledger_tail(last_n: int = 20) -> dict[str, Any]:
        return service.ledger_tail(last_n=last_n)
