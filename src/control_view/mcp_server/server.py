from __future__ import annotations

from fastmcp import FastMCP

from control_view.mcp_server.tools import register_tools
from control_view.service import ControlViewService


def build_server(service: ControlViewService) -> FastMCP:
    server = FastMCP("control-view-sidecar")
    register_tools(server, service)
    return server

