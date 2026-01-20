# Durability Call Graph (Code-Verified)

This call graph shows the durability-related control flow in the CLI.

## Core Loop
```
main()
  -> setup_session()
  -> process_turn()
     -> run_conversation()
        -> ToolUseBlock (PreToolUse hooks)
           -> on_pre_tool_use_ledger()
              -> prepare_tool_call()
              -> mark_running()
              -> checkpoint updates
        -> ToolResultBlock (PostToolUse hooks)
           -> on_post_tool_use_ledger()
              -> mark_succeeded()
              -> replay status updates
```

## Resume / Replay
```
--resume
  -> build_resume_packet()
  -> reconcile_inflight_tools()
     -> load in-flight tool calls
     -> replay_policy classification
     -> forced replay loop
     -> post_replay checkpoint
```

## Task Relaunch
```
reconcile_inflight_tools()
  -> if replay_policy == RELAUNCH:
       -> _ensure_task_key()
       -> reuse subagent_output.json OR
       -> reuse output paths from Task prompt
       -> else enqueue relaunch tool call
```

## Guardrails
```
on_pre_tool_use_ledger()
  -> tool-name sanitization
  -> identity recipient resolution
  -> durable job mode blocks
  -> schema guardrails
```

