"""Lazy console entrypoint for the optional MCP integration."""

from __future__ import annotations

import sys


def main() -> None:
    """Run the MCP server or explain how to install its optional dependency."""
    try:
        from .mcp_server import main as run_server
    except ModuleNotFoundError as error:
        if error.name != "mcp":
            raise
        print(
            "MCP support is not installed. From the repository, run: "
            "uv sync --extra mcp",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    run_server()


if __name__ == "__main__":
    main()
