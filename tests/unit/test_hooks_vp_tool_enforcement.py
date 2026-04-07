import asyncio

from universal_agent.hooks import AgentHookSet


def _run(coro):
    return asyncio.run(coro)


def test_blocks_task_when_user_prompt_explicitly_requests_general_vp():
    hooks = AgentHookSet(run_id="unit-vp-enforcement")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Hi Simone, use the General VP to write a poem."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Create a poem via VP",
                    "prompt": "You are the General VP. Create a poem.",
                },
            },
            "tool-1",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "vp_dispatch_mission" in str(result.get("systemMessage", ""))


def test_blocks_task_when_user_prompt_uses_general_vp_alias():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-general-vp")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Simone, use the general VP to create a poem and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Create poem",
                    "prompt": "You are the General VP. Write a poem.",
                },
            },
            "tool-general-vp",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "vp_dispatch_mission" in str(result.get("systemMessage", ""))


def test_blocks_task_when_user_prompt_uses_vp_general_word_order():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-vp-general-order")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the VP general agent to write a story and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Create story",
                    "prompt": "You are the General VP. Write a story.",
                },
            },
            "tool-vp-general-order",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "vp_dispatch_mission" in str(result.get("systemMessage", ""))


def test_blocks_task_when_payload_tries_general_vp_without_explicit_turn_state():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-payload")
    _run(hooks.on_user_prompt_skill_awareness({"prompt": "Write a poem and email it to me."}))

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Delegate to General VP",
                    "prompt": "You are the General VP. Create a poem.",
                },
            },
            "tool-2",
            {},
        )
    )

    assert result.get("decision") == "block"

    followup = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo fallback"},
            },
            "tool-2b",
            {},
        )
    )
    assert followup.get("decision") == "block"
    assert "First tool call in this turn must be `vp_dispatch_mission" in str(
        followup.get("systemMessage", "")
    )


def test_allows_task_after_vp_dispatch_in_same_turn():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-dispatch")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the General VP to produce the result."}
        )
    )

    dispatch_result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "vp_dispatch_mission",
                "tool_input": {"vp_id": "vp.general.primary", "objective": "Create a poem"},
            },
            "tool-dispatch",
            {},
        )
    )
    assert dispatch_result == {}

    task_result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "general-purpose",
                    "description": "Fallback path",
                    "prompt": "regular delegation",
                },
            },
            "tool-task",
            {},
        )
    )
    assert task_result == {}


def test_blocks_non_vp_tool_before_dispatch_when_prompt_has_explicit_vp_intent():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-pre-dispatch")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the General VP to create a poem and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "src/universal_agent/vp/profiles.py"},
            },
            "tool-read-before-dispatch",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "First tool call" in str(result.get("systemMessage", ""))


def test_allows_task_when_no_explicit_vp_intent():
    hooks = AgentHookSet(run_id="unit-vp-enforcement-normal")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Research cloud costs and summarize key points."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "description": "Gather cloud pricing details",
                    "prompt": "Collect 2026 prices from official docs.",
                },
            },
            "tool-3",
            {},
        )
    )
    assert result == {}


def test_vp_worker_lane_does_not_require_nested_vp_dispatch():
    hooks = AgentHookSet(
        run_id="unit-vp-worker-lane-bypass",
        active_workspace=(
            "/tmp/AGENT_RUN_WORKSPACES/"
            "vp_general_primary_external/vp-mission-1234567890abcdef"
        ),
    )
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Use the VP general to create a story and email it."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/foo.md"},
            },
            "tool-read-allowed-in-vp-worker-lane",
            {},
        )
    )
    assert result == {}


def test_todo_dispatcher_source_does_not_infer_vp_routing_from_prompt_boilerplate(monkeypatch):
    monkeypatch.setenv("UA_RUN_SOURCE", "todo_dispatcher")
    hooks = AgentHookSet(run_id="unit-vp-enforcement-todo-source")
    prompt = """
You are Simone.
### VP Delegation Fallback (CRITICAL):
If you attempt to delegate a mission to a VP Gateway (e.g., `vp.general.primary` or `vp.coder.primary`) and the connection is refused, retry it.

Task 1: Research cloud costs and summarize key points.
"""
    _run(hooks.on_user_prompt_skill_awareness({"prompt": prompt}))

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "description": "Gather cloud pricing details",
                    "prompt": "Collect 2026 prices from official docs.",
                },
            },
            "tool-todo-no-vp",
            {},
        )
    )

    assert result == {}


def test_blocks_primary_search_before_research_specialist_for_report_intent():
    hooks = AgentHookSet(run_id="unit-research-delegation-block-search")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "Search for the latest information from the Russia-Ukraine war, "
                    "create a report, save as PDF, and email it to me."
                )
            }
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__composio__COMPOSIO_SEARCH_NEWS",
                "tool_input": {"query": "Russia Ukraine latest developments"},
            },
            "tool-search-before-task",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "research-specialist" in str(result.get("systemMessage", ""))


def test_blocks_wrong_first_task_for_report_intent():
    hooks = AgentHookSet(run_id="unit-research-delegation-block-wrong-task")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Research latest AI developments and create a report in PDF format."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "report-writer",
                    "prompt": "Write the report now.",
                },
            },
            "tool-wrong-task",
            {},
        )
    )

    assert result.get("decision") == "block"


def test_code_change_prompt_with_research_terms_allows_code_writer_delegate():
    hooks = AgentHookSet(run_id="unit-code-change-not-research")
    prompt = (
        "Implement a new feature in our repo and fix our code so Simone can delegate to Cody. "
        "The pasted idea file below mentions research, reports, and analysis, but this task is a code implementation request.\n\n"
        "# LLM Wiki\n\n"
        "This can apply to a lot of different contexts, including research. "
        "Ask a subtle question that requires synthesizing five documents and the LLM has to find and piece together the relevant fragments every time. "
        "Query. Lint. Research. Report. Analysis."
    )
    _run(hooks.on_user_prompt_skill_awareness({"prompt": prompt}))

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "code-writer",
                    "description": "Implement the repo change",
                    "prompt": "Make the code changes in the repository.",
                },
            },
            "tool-code-change",
            {},
        )
    )

    assert result == {}


def test_allows_research_specialist_as_first_task_for_report_intent():
    hooks = AgentHookSet(run_id="unit-research-delegation-allow-task")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Search recent cybersecurity news and create a report."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "prompt": "Collect and refine sources.",
                },
            },
            "tool-research-task",
            {},
        )
    )

    assert result == {}


def test_allows_followup_tools_after_research_specialist_delegation():
    hooks = AgentHookSet(run_id="unit-research-delegation-followup")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Search market updates and write a report with a PDF."}
        )
    )

    first = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "prompt": "Gather sources.",
                },
            },
            "tool-task-first",
            {},
        )
    )
    assert first == {}

    second = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__composio__COMPOSIO_SEARCH_NEWS",
                "tool_input": {"query": "market updates"},
            },
            "tool-search-after-task",
            {},
        )
    )
    assert second == {}


def test_allows_notebooklm_operator_as_first_task_for_notebooklm_research_intent():
    hooks = AgentHookSet(run_id="unit-notebooklm-delegation-allow-task")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "Use our NotebookLM workflow for this task. Research the latest information "
                    "from the Russia-Ukraine war, create a report, save as PDF, and email it."
                )
            }
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "notebooklm-operator",
                    "description": "NotebookLM workflow",
                    "prompt": "Run the NotebookLM research demo.",
                },
            },
            "tool-notebooklm-task",
            {},
        )
    )

    assert result == {}


def test_allows_notebooklm_skill_before_operator_for_notebooklm_research_intent():
    hooks = AgentHookSet(run_id="unit-notebooklm-delegation-allow-skill")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "Use our NotebookLM workflow for this task. Research the latest information "
                    "from the Russia-Ukraine war, create a report, save as PDF, and email it."
                )
            }
        )
    )

    skill_result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Skill",
                "tool_input": {
                    "skill": "notebooklm-orchestration",
                    "args": "Run the NotebookLM workflow end to end.",
                },
            },
            "tool-notebooklm-skill",
            {},
        )
    )
    assert skill_result == {}

    task_result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "notebooklm-operator",
                    "description": "NotebookLM workflow",
                    "prompt": "Run the NotebookLM research demo.",
                },
            },
            "tool-notebooklm-task-after-skill",
            {},
        )
    )
    assert task_result == {}


def test_information_prompt_enforces_research_delegate_first_even_without_report():
    hooks = AgentHookSet(run_id="unit-research-delegation-info-only")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Search for the latest weather forecast in New York."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__composio__COMPOSIO_SEARCH_NEWS",
                "tool_input": {"query": "weather forecast NYC"},
            },
            "tool-search-non-report",
            {},
        )
    )
    assert result.get("decision") == "block"
    assert "research-specialist" in str(result.get("systemMessage", ""))


def test_delivery_only_creative_prompt_does_not_enforce_research_delegate_first():
    hooks = AgentHookSet(run_id="unit-research-delegation-delivery-only")
    _run(
        hooks.on_user_prompt_skill_awareness(
            {"prompt": "Write a poem and then email it to me."}
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__composio__COMPOSIO_SEARCH_TOOLS",
                "tool_input": {
                    "queries": [{"use_case": "send an email"}],
                },
            },
            "tool-delivery-only",
            {},
        )
    )
    assert result == {}


def test_todo_execution_manifest_enforces_research_delegate_first_without_prompt_keywords():
    hooks = AgentHookSet(run_id="unit-todo-manifest-research")
    hooks._resolved_run_kind = "todo_execution"
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "You are Simone.\n"
                    "== EXECUTION MANIFEST ==\n"
                    "workflow_kind=research_report_email\n"
                    "delivery_mode=standard_report\n"
                    "requires_pdf=true\n"
                    "final_channel=email\n"
                    "canonical_executor=simone_first\n\n"
                    "Work Item 1: [email:1] Please help with this request."
                )
            }
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__composio__COMPOSIO_SEARCH_TOOLS",
                "tool_input": {"queries": [{"use_case": "search news"}]},
            },
            "tool-search-before-research-task",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "research-specialist" in str(result.get("systemMessage", ""))


def test_todo_execution_interactive_manifest_does_not_infer_research_from_prompt_boilerplate():
    hooks = AgentHookSet(run_id="unit-todo-manifest-interactive")
    hooks._resolved_run_kind = "todo_execution"
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "You are Simone, the Pipeline Orchestrator.\n"
                    "For standard_report and enhanced_report: send exactly one final email with an executive summary.\n"
                    "== EXECUTION MANIFEST ==\n"
                    "workflow_kind=interactive_answer\n"
                    "delivery_mode=interactive_chat\n"
                    "requires_pdf=false\n"
                    "final_channel=chat\n"
                    "canonical_executor=simone_first\n\n"
                    "Work Item 1: [chat:1] Write a short weather poem for Houston."
                )
            }
        )
    )

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__composio__COMPOSIO_SEARCH_TOOLS",
                "tool_input": {"queries": [{"use_case": "find a weather source"}]},
            },
            "tool-search-after-interactive-manifest",
            {},
        )
    )

    assert result == {}


def test_email_triage_prompt_does_not_infer_research_from_triage_boilerplate():
    hooks = AgentHookSet(run_id="unit-email-triage-no-research-inference")
    hooks._resolved_run_kind = "email_triage"
    _run(
        hooks.on_user_prompt_skill_awareness(
            {
                "prompt": (
                    "== EMAIL TRIAGE SESSION ==\n"
                    "This is a TRIAGE-ONLY email processing session.\n"
                    "The inbound email has already been materialized into Task Hub.\n"
                    "The dedicated ToDo executor is the canonical owner of execution and final delivery.\n"
                    "Your ONLY job in this hook run is to return a structured triage brief as plain text.\n"
                    "Do not delegate, do not create tasks, do not persist files, and do not send replies from this session.\n"
                    "Hard constraints:\n"
                    "- Do NOT use Task(...) or Agent(...) in this session.\n"
                    "- Do NOT run research, do NOT dispatch VP missions, and do NOT send the final deliverable.\n"
                    "━━━ EMAIL PAYLOAD ━━━\n"
                    "Hi Simone,\n"
                    "Get the weather forecast in Houston for the weekend.\n"
                    "Search for any fun adult activities for tomorrow.\n"
                    "Get me a special roasted carrots recipe for Easter dinner.\n"
                    "Email all these to me.\n"
                )
            }
        )
    )

    assert hooks._requires_research_delegate_first is False
    assert hooks._requires_vp_tool_path is False
    assert hooks._notebooklm_intent_this_turn is False


def test_todo_execution_blocks_ask_user_questions_and_requires_durable_disposition():
    hooks = AgentHookSet(run_id="unit-todo-no-human-question")
    hooks._resolved_run_kind = "todo_execution"

    result = _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "mcp__internal__ask_user_questions",
                "tool_input": {
                    "questions": "Please resolve this policy conflict.",
                },
            },
            "tool-human-question",
            {},
        )
    )

    assert result.get("decision") == "block"
    assert "task_hub_task_action" in str(result.get("systemMessage", ""))
