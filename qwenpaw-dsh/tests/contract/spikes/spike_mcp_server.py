# -*- coding: utf-8 -*-
"""A tiny stdio MCP server used by spike 05.

Exposes one tool, ``ping`` -> ``PONG``.  Run standalone to sanity-check:

    ~/.qwenpaw/venv/bin/python spikes/spike_mcp_server.py   # speaks MCP on stdio
"""

from __future__ import annotations


def build():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("spike")

    @mcp.tool()
    def ping() -> str:
        """Return the literal string PONG."""
        return "PONG"

    return mcp


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
