# -*- coding: utf-8 -*-
"""Gate-7 acceptance fixture: a local **streamable-HTTP** MCP server.

Exposes one tool so an end-to-end run can prove that the QwenPaw adapter
projects an ``http`` / ``streamable_http`` MCP card into ACP
``session/new.mcpServers`` and that DSH actually connects and calls it.

    ~/.qwenpaw/venv/bin/python http_mcp_server.py --port 8931

Tool: ``http_ping`` -> ``PONG-HTTP`` (no arguments).
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gate7-http")


@mcp.tool()
def http_ping() -> str:
    """Return the gate-7 HTTP transport marker."""
    return "PONG-HTTP"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8931)
    args = parser.parse_args()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
