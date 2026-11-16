from __future__ import annotations

import json
import sys
from pathlib import Path

from muse.ploid_adapter import PloidStdioTransport


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    # Native Windows .cmd launchers need cmd.exe when subprocess uses shell=False.
    command = ("cmd.exe", "/d", "/s", "/c", "npx -y @ploid/mcp")
    transport = PloidStdioTransport(command, workspace, timeout_seconds=35)
    try:
        tools = transport.list_tools()
    except Exception as exc:
        # Never print raw stderr or exception text: it may contain environment details.
        print(json.dumps({
            "status": "not_connected",
            "error_type": type(exc).__name__,
            "tools_call_performed": False,
            "paid_operation_performed": False,
            "message": "MCP initialize/tools/list did not complete. No Ploid research tool was called.",
        }, ensure_ascii=False, indent=2))
        return 2

    expected = {
        "research_person", "find_people", "find_decision_makers",
        "get_research_status", "cancel_research",
    }
    names = sorted({
        item.get("name") for item in tools
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    })
    schema = []
    for item in tools:
        if not isinstance(item, dict) or item.get("name") not in expected | {"search_people"}:
            continue
        input_schema = item.get("inputSchema")
        if not isinstance(input_schema, dict):
            input_schema = {}
        properties = input_schema.get("properties")
        schema.append({
            "name": item["name"],
            "required": input_schema.get("required", []),
            "properties": sorted(properties) if isinstance(properties, dict) else [],
            "additionalProperties": input_schema.get("additionalProperties"),
        })
    missing = sorted(expected - set(names))
    print(json.dumps({
        "status": "connected" if not missing else "connected_schema_mismatch",
        "tools": schema,
        "all_tool_names": names,
        "missing_expected_tools": missing,
        "tools_call_performed": False,
        "paid_operation_performed": False,
        "credential_values_read_or_printed": False,
    }, ensure_ascii=False, indent=2))
    return 0 if not missing else 3


if __name__ == "__main__":
    raise SystemExit(main())
