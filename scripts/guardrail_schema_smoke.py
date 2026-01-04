"""Smoke test for tool schema guardrails.
Run: uv run python scripts/guardrail_schema_smoke.py
"""

import asyncio

from universal_agent.guardrails.tool_schema import pre_tool_use_schema_guardrail


def main() -> None:
    result = asyncio.run(
        pre_tool_use_schema_guardrail(
            {
                "tool_name": "mcp__local_toolkit__write_local_file",
                "tool_input": {},
            }
        )
    )

    if not result:
        raise SystemExit("Expected schema guardrail to block missing args.")

    decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
    if decision != "deny":
        raise SystemExit("Expected permissionDecision=deny.")

    print("✅ Guardrail smoke test passed.")


if __name__ == "__main__":
    main()
