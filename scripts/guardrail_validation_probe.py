import asyncio

import logfire

from universal_agent.guardrails.tool_schema import (
    post_tool_use_schema_nudge,
    pre_tool_use_schema_guardrail,
)


async def main() -> None:
    logfire.configure()
    pre_result = await pre_tool_use_schema_guardrail(
        {"tool_name": "write_local_file", "tool_input": {}},
        run_id="guardrail-probe",
        step_id="schema",
        logger=logfire,
    )
    print("pre_tool_use_schema_guardrail:", pre_result)

    post_result = await post_tool_use_schema_nudge(
        {
            "tool_name": "write_local_file",
            "tool_response": "validation error: Field required",
            "is_error": True,
        },
        run_id="guardrail-probe",
        step_id="schema",
        logger=logfire,
    )
    print("post_tool_use_schema_nudge:", post_result)


if __name__ == "__main__":
    asyncio.run(main())
