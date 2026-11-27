"""One-tool server/client proof (brief, suggested approach steps 2-3; deliverable #2).

Connects to the running EquipDesk MCP server over Streamable HTTP, lists its
tools and calls get_employee_info.

    make smoke                                    # inside the running api container
    python scripts/smoke_client.py --url http://localhost:8000/mcp --employee-id E1001

Exit code: 0 on success, 1 if the tool returned an error, 2 if the server could
not be reached.
"""

import argparse
import json
import sys
from typing import Any

import anyio
from mcp import Client
from mcp.types import CallToolResult, TextContent

DEFAULT_URL = "http://localhost:8000/mcp"


def read_payload(result: CallToolResult) -> Any:
    """The tool's answer: parsed JSON text, the raw text if it is not JSON (e.g. an
    MCP-level "Unknown tool" error), else the structured content, else None."""
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    if texts:
        try:
            return json.loads(texts[0])
        except ValueError:
            return texts[0]
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return None


async def _run(url: str, employee_id: str) -> CallToolResult:
    async with Client(url) as client:
        print(f"Connected to {url}")
        tools = (await client.list_tools()).tools
        print(f"Tools: {', '.join(sorted(tool.name for tool in tools))}")

        print(f"\n>>> get_employee_info(employee_id={employee_id!r})")
        return await client.call_tool("get_employee_info", {"employee_id": employee_id})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Call get_employee_info on the EquipDesk MCP server."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP endpoint (default {DEFAULT_URL})")
    parser.add_argument(
        "--employee-id", default="E1001", help="employee to look up (default E1001)"
    )
    args = parser.parse_args(argv)

    try:
        result = anyio.run(_run, args.url, args.employee_id)
    except Exception as exc:  # connection refused, DNS, HTTP or MCP session failures
        print(f"Could not reach {args.url} or complete the MCP session: {exc!r}", file=sys.stderr)
        return 2

    # The server answered: show its reply as is; any error in it is exit 1.
    payload = read_payload(result)
    print(payload if isinstance(payload, str) else json.dumps(payload, indent=2))
    if result.is_error or not isinstance(payload, dict) or "error" in payload:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
