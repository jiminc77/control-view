from __future__ import annotations

from fastmcp import FastMCP

from control_view.mcp_server.model_tools import register_model_tools
from control_view.mcp_server.tools import register_tools
from control_view.mcp_server.transcript_tools import register_transcript_tools
from control_view.service import ControlViewService


def build_server(
    service: ControlViewService,
    *,
    tool_surface: str = "full",
    baseline_policy: str = "B3",
) -> FastMCP:
    server = FastMCP("control-view-sidecar")
    if tool_surface == "model":
        register_model_tools(server, service)
    elif tool_surface == "thin":
        register_transcript_tools(server, service, baseline_policy=baseline_policy)
    else:
        register_tools(server, service)
        register_transcript_tools(server, service, baseline_policy=baseline_policy)
    return server
