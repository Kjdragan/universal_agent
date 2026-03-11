# NotebookLM VPS Baseline Run (Without notebooklm-orchestration skill instructions)

## Execution Mode
- Baseline run completed directly through shell/CLI and local repo code.
- No dependency on `notebooklm-orchestration` skill instructions.

## Setup Performed
1. Verified NotebookLM tooling is installed on this runtime:
- `nlm` present at `/home/kjdragan/.local/bin/nlm`
- `notebooklm-mcp` present at `/home/kjdragan/.local/bin/notebooklm-mcp`

2. Configured NotebookLM MCP for Codex CLI:
- Ran: `nlm setup add codex`
- Verified with: `codex mcp list` (shows `notebooklm-mcp` enabled in Codex config)

## Auth Verification (`vps` profile)
1. Initial check before setup:
- `nlm login --check --profile vps` failed with `Profile not found: vps`

2. Established/updated `vps` auth profile:
- Ran: `nlm login --profile vps --force`
- Result: authenticated as `kevinjdragan@gmail.com`, credentials saved under NotebookLM profile `vps`

3. Final verification:
- `nlm login --check --profile vps` succeeded
- Preflight script succeeded:
  - Command: `PYTHONPATH=src uv run python scripts/notebooklm_auth_preflight.py --workspace <outputs> --timeout 45`
  - Result payload: `{"ok": true, "profile": "vps", "seeded": false, "refreshed": false}`

## Notebook Listing + MCP Fallback
1. MCP path attempted first:
- `list_mcp_resources(server="notebooklm-mcp")`
- Result: `unknown MCP server 'notebooklm-mcp'` in this active agent session

2. Fallback path used (successful):
- `nlm list notebooks --profile vps --json`

- Run timestamp: 2026-03-09T19:59:24-05:00
- Notebook count: **87**

## Notebooks (from fallback CLI listing)
- 1. Q2 Strategy | id=`37d22235-d02b-4a9a-aee3-f1c0f0bc552e` | sources=1 | updated=2026-03-10T00:58:49Z
- 2. Q2 Strategy | id=`f94d86c2-6a2b-418a-b65a-ccc04cd6c46c` | sources=1 | updated=2026-03-10T00:58:39Z
- 3. Claude Agent SDK Bible | id=`6e67232c-8a23-4824-884d-de9b3a4eb06d` | sources=81 | updated=2026-03-07T02:46:23Z
- 4. Three Tier Architecture for AI Agent Design | id=`71c2b454-6a5e-4562-b6bf-454fe736278b` | sources=1 | updated=2026-03-04T14:08:50Z
- 5. Shared LoRA Subspaces for Strict Continual Learning | id=`553f1932-a75f-487c-803f-71c968e1420e` | sources=1 | updated=2026-02-23T17:14:38Z
- 6. Maximizing Productivity with Moltbot: A Guide to Essential AI Skills | id=`0696e320-c861-4cfa-9e90-e650370966da` | sources=1 | updated=2026-01-31T02:19:35Z
- 7. Building General AI Agents with Claude Code Skills | id=`dc1c83c9-12c4-4e51-84de-c3af16145513` | sources=1 | updated=2026-01-24T16:05:03Z
- 8. WebSync: settings | id=`0b666b61-96bb-4353-9708-0bd5cb2244a8` | sources=0 | updated=2026-01-24T15:58:48Z
- 9. Building Better: Four Principles of AI System Architecture | id=`5cf2310b-77de-402b-aaea-8eaf8c1751c5` | sources=1 | updated=2026-01-21T02:09:50Z
- 10. Claude Co-work: Agentic File Management and Workflow Automation | id=`bccf55c4-24df-4b22-ba7f-ce7c30b7a626` | sources=2 | updated=2026-01-21T01:57:44Z
- 11. Claude Co-work: Building Your Daily AI Operating System | id=`299afa35-63e4-4b6a-b33b-ba6938220b84` | sources=1 | updated=2026-01-21T01:56:38Z
- 12. Extending Claude’s Memory with Eternal Note-Taking Cycles | id=`0f1ec71b-7f5a-4fba-b193-5b7b575609a2` | sources=5 | updated=2026-01-17T00:04:12Z
- 13. (untitled) | id=`5222265f-02d0-46c3-898c-9448a56cbb07` | sources=0 | updated=2026-01-17T00:04:02Z
- 14. Recursive Language Models for Infinite Context Scaling | id=`17645bb6-d2d0-4ef9-8da0-826c63b6cbe4` | sources=1 | updated=2026-01-16T16:57:23Z
- 15. Gateways to the Google Ecosystem | id=`4e4d9ec5-53e6-458e-ad76-d7ec9c8ce040` | sources=1 | updated=2026-01-15T00:58:52Z
- 16. Claude Code Ralph Wiggum Plugin Repository Overview | id=`2f558ae8-bb46-4ac3-b7db-17d7a038e931` | sources=1 | updated=2026-01-05T17:08:38Z
- 17. Nested Learning and Optimization via Associative Memory Systems | id=`7a803fd8-d26b-4d55-a4bc-88743681baae` | sources=1 | updated=2026-01-03T20:29:35Z
- 18. Democritus: Building Large Causal Models from Large Language Models | id=`c1f8ecd5-b140-401d-9c8e-12482847e213` | sources=1 | updated=2025-12-29T17:29:27Z
- 19. LLMs and the Causal Ladder: Frontiers in Causal Discovery | id=`c259cc0d-2d4d-418b-b4e3-09097f4b7bdd` | sources=7 | updated=2025-12-29T17:27:48Z
- 20. Safeguarding Autonomous AI Code Agents with Dev Containers | id=`048a671f-dc94-4676-a84d-d44feb0e63fe` | sources=1 | updated=2025-12-29T13:14:23Z
- 21. Building an Iterative Feedback Loop with LangSmith and Code Agents | id=`d7db3936-ea5b-4e43-9524-e7618d88f5ca` | sources=1 | updated=2025-12-27T04:12:46Z
- 22. System Prompt Learning for Optimizing Code Agents | id=`2fcab219-dff7-444a-9a2e-5bff1faefb7c` | sources=1 | updated=2025-12-25T06:37:30Z
- 23. Dynamic Memory Patterns for Deep Agents | id=`574cba18-983a-4411-9580-cf9f69ba5dad` | sources=1 | updated=2025-12-25T06:33:14Z
- 24. The Blueprint for Building Boring AI Cash Cows | id=`66ac9c06-9b12-4955-bfc7-c6c389f51ee3` | sources=1 | updated=2025-12-25T06:28:50Z
- 25. (untitled) | id=`6cbcb163-d03e-4d2e-8888-2ca3953d175b` | sources=0 | updated=2025-12-25T06:27:17Z
- 26. (untitled) | id=`e8f55f96-b84b-4f19-9b73-258c3ae3437f` | sources=0 | updated=2025-12-25T06:26:43Z
- 27. Beyond the Hype: The Pragmatic AI Era of 2026 | id=`eed78f09-2e22-4008-9718-3ddf4c51da4d` | sources=1 | updated=2025-12-25T06:13:49Z
- 28. Building PAI 2.0: Personal AI Scaffolding and Human Augmentation | id=`fd9d6a5e-5be1-47b8-ac00-c8dcd968653d` | sources=1 | updated=2025-12-25T06:13:40Z
- 29. Domain Memory: The Key to Durable AI Agents | id=`5ae92aae-ad3e-4eba-bdb9-559e12f620eb` | sources=1 | updated=2025-12-21T06:52:41Z
- 30. Beyond the Transformer: Five Breakthroughs Redefining the AI Future | id=`3860cfc5-31c4-47b7-a5bf-fe9406acbc0d` | sources=1 | updated=2025-12-20T00:15:52Z
- 31. Gemini Interactions API: Unified Interface and Capabilities | id=`4e98315e-6025-4fb1-81b8-0591612c774b` | sources=4 | updated=2025-12-17T06:55:31Z
- 32. Letta AI Learning SDK for Stateful LLM Agents | id=`f2d0fe8d-ee61-4fb3-ac95-6cd8a68e2596` | sources=1 | updated=2025-12-15T22:44:24Z
- 33. Claude Agent SDK: Loops, Delegation, and Hooks | id=`49e57656-c679-4ea7-a66e-cf59b79ad443` | sources=1 | updated=2025-12-15T16:46:29Z
- 34. 15 Rules for Optimal Claude Code Vibe Coding | id=`52223152-cd22-447e-8479-7f8968f2637f` | sources=1 | updated=2025-12-15T00:58:07Z
- 35. Pydantic for Robust AI and LLM Engineering | id=`2f85023e-accc-47ee-be83-ff0d330f6d5e` | sources=1 | updated=2025-12-15T00:57:57Z
- 36. Claude Agent SDK: Overview and Capabilities | id=`69bee9ac-95ab-4a96-9d9e-50bc3216a6e2` | sources=22 | updated=2025-12-15T00:41:22Z
- 37. Viral PowerPoints: NotebookLM Prompt Framework | id=`1fa91995-406a-457c-9f20-3b361cf03671` | sources=1 | updated=2025-12-15T00:41:04Z
- 38. Gemini 3.0 Pro 4-Step UI Design System | id=`01100078-c3f8-4426-9a65-1a70898315fe` | sources=1 | updated=2025-12-13T19:39:42Z
- 39. AI-Driven Skill Practice for Knowledge Work | id=`eeaaf391-3a2e-4edf-8ce7-9f95a75c0411` | sources=1 | updated=2025-12-13T06:32:15Z
- 40. (untitled) | id=`fec2539a-ee0e-47d2-8dd1-6e8582861cbd` | sources=0 | updated=2025-12-13T05:13:23Z
- 41. Implementing Agent Skills in the Claude SDK | id=`d409e75d-b0b8-4d40-941a-f648452216be` | sources=3 | updated=2025-12-13T04:18:19Z
- 42. Claude's Persistent Memory Tool and Management | id=`703519fb-0473-4839-a17a-eea699fdc772` | sources=3 | updated=2025-12-13T04:16:58Z
- 43. (untitled) | id=`3372aa27-a0dc-47db-8f29-e3394502e969` | sources=0 | updated=2025-12-13T04:11:20Z
- 44. The Decoupling of Software: Durable Substrates and Disposable Pixels | id=`35388d1d-97a9-4def-aad9-62bed78c515f` | sources=1 | updated=2025-12-11T06:53:07Z
- 45. The Future of Software: Disposable Pixels and Agent Substrates | id=`2bc51937-2964-4d4c-af55-1947ddef7377` | sources=1 | updated=2025-12-11T06:51:38Z
- 46. Agentic Context Engineering: Memory Systems for Production AI | id=`3f32008a-60e2-4505-a965-8574f603dad6` | sources=1 | updated=2025-12-11T06:49:57Z
- 47. Agent Memory and Domain-Specific Harnesses | id=`c6be4d33-61b1-4a9f-bd3d-0d171c674dec` | sources=1 | updated=2025-12-11T06:48:14Z
- 48. AI Fluency: Strategy, Skills, and Evaluation with AI Cred | id=`fd100ec9-2d74-4956-bf95-c19d7d8fb636` | sources=1 | updated=2025-12-11T06:47:27Z
- 49. Pydantic 2.12: Features for Data Validation and AI Systems | id=`37e91f8d-2deb-4e77-8c79-14f4fa420fe1` | sources=1 | updated=2025-12-11T06:36:15Z
- 50. Seven Architectural Patterns for Claude Agent Systems | id=`94fa61df-93a2-481b-ba21-c3a8b2dd366a` | sources=1 | updated=2025-12-11T06:34:44Z
- 51. DeepSeek-V3.2: Efficient Open LLM with Gold-Medal Reasoning | id=`79eff22f-fdbc-4781-aa75-3686b03911a8` | sources=1 | updated=2025-12-10T03:33:14Z
- 52. (untitled) | id=`1dcd1d25-ec08-4298-95c6-65753b31a389` | sources=0 | updated=2025-12-04T07:10:57Z
- 53. Huxley-Gödel Machine: Optimal Self-Improving Coding Agent | id=`49b2c631-38da-4abb-8dbe-51e676cfa660` | sources=2 | updated=2025-10-28T05:09:00Z
- 54. ParaThinker: Native Parallel Thinking for LLMs | id=`0580fe03-b14d-496c-9658-b36f401b332c` | sources=1 | updated=2025-09-10T14:03:44Z
- 55. Why Language Models Hallucinate | id=`18043106-751c-45ae-9f3d-44242beedbbd` | sources=1 | updated=2025-09-09T20:42:51Z
- 56. Muon's Scalability for LLM Training and Moonlight MoE Model | id=`696104e5-5431-495e-be74-7da3ffd7d41d` | sources=1 | updated=2025-08-31T01:21:16Z
- 57. Self-Guided Diffusion Models | id=`54f2e4cb-9cfa-4f4f-9a77-c16f68c9d295` | sources=1 | updated=2025-08-19T04:47:19Z
- 58. Pleiades: Epigenome Foundation Models for Clinical Genomics | id=`2b4a75bb-cafd-4319-9e9c-700dfe0e7266` | sources=1 | updated=2025-08-16T04:38:36Z
- 59. MLE-STAR: AI Agent for Machine Learning Engineering | id=`695b6ce5-fe01-4598-83be-5da708ef13c3` | sources=1 | updated=2025-08-05T20:07:43Z
- 60. Thinking Beyond Tokens: The Path to AGI | id=`36756dfc-a315-4534-a013-965161b19318` | sources=1 | updated=2025-07-03T12:35:55Z
- 61. AutoSchemaKG: Autonomous Knowledge Graph Construction with LLMs | id=`bf791ee9-e7e2-4a26-a6dd-a3162d368fe2` | sources=1 | updated=2025-06-27T14:30:15Z
- 62. The Latent Space Hypothesis: Medical Representation Learning | id=`40894d4c-4717-44c4-b511-9485a2fc70a2` | sources=1 | updated=2025-06-08T03:00:13Z
- 63. Learning in Latent Space with Generative Models | id=`c7e71246-ad7f-4d5a-babc-65dbfb878b29` | sources=1 | updated=2025-06-03T03:04:56Z
- 64. Scaling Law Implications of Multimodal Alignment | id=`d36d6856-22b9-43da-93e5-8dc2b3a98bf7` | sources=1 | updated=2025-06-03T03:04:17Z
- 65. LLaDA: Language Diffusion Models Challenge ARMs | id=`15e98d92-1bc5-4071-8f51-8feadd077361` | sources=1 | updated=2025-05-25T02:16:12Z
- 66. LangGraph Agent with Supabase Edge Function Persistence | id=`e7fcd336-beea-480a-8df9-1e3df9bc4112` | sources=1 | updated=2025-03-21T01:26:45Z
- 67. Kimi k1.5: Scaling LLMs with Reinforcement Learning and Long Context | id=`2f9701f0-7259-4f8c-8147-d840e489418b` | sources=1 | updated=2025-02-27T17:38:02Z
- 68. s1: Simple Test-Time Scaling for Reasoning in Language Models | id=`d9fd9490-72b3-4b62-a32a-c028bf30a71b` | sources=1 | updated=2025-02-13T03:44:02Z
- 69. Multi-Agent System Design: Optimizing Prompts and Topologies | id=`99cfd34c-e310-43d4-8ea8-e718269bd000` | sources=1 | updated=2025-02-08T18:44:57Z
- 70. Inference-Aware Fine-Tuning for Best-of-N LLM Sampling | id=`afe95421-8cec-44d7-bbaf-ef31ce698940` | sources=1 | updated=2025-01-20T20:48:29Z
- 71. Transformer2: Self-Adaptive LLMs | id=`1b6e3cb2-2624-4795-8b0f-9b1d02eac4c0` | sources=1 | updated=2025-01-20T20:48:23Z
- 72. AI Research | id=`95c7a97a-da87-42e9-980d-d2a1e0fb514a` | sources=40 | updated=2025-01-20T02:13:03Z
- 73. rStar-Math: Self-Evolved Deep Thinking for Math Reasoning in Small LLMs | id=`72aeaef3-b744-44e7-a350-c8114a67d02a` | sources=1 | updated=2025-01-16T14:48:55Z
- 74. Health and Diet | id=`9282506d-2247-42bb-9536-644c1574b5c1` | sources=1 | updated=2024-12-17T15:06:32Z
- 75. Untitled notebook | id=`b464c929-7624-40fa-a15f-2a5b094ae0c0` | sources=1 | updated=2024-11-16T18:34:23Z
- 76. Untitled notebook | id=`e508d679-1675-40e4-ad2a-d2a6b7a029b1` | sources=0 | updated=2024-11-16T18:24:28Z
- 77. Untitled notebook | id=`8a220277-e114-4867-8af8-d74795e45e70` | sources=1 | updated=2024-11-01T21:22:44Z
- 78. Untitled notebook | id=`f5ac102e-fd5f-4fe5-a805-6c8f08f8a610` | sources=1 | updated=2024-10-28T01:14:29Z
- 79. Knowledge Graphs | id=`6df154d5-711d-420e-a8de-c0229115964f` | sources=3 | updated=2024-10-17T13:29:51Z
- 80. Jack Smith Filing | id=`93f21688-fdf6-4315-8755-240073c64c33` | sources=1 | updated=2024-10-03T13:45:39Z
- 81. Untitled notebook | id=`dd8f3517-a49a-40e4-b38f-2b673d5e8a4c` | sources=0 | updated=2024-10-03T05:23:50Z
- 82. Kelsey Science Research | id=`bdcf1bc2-53dd-48c7-8190-4fa5dbb38e1d` | sources=1 | updated=2024-09-17T13:00:41Z
- 83. Work ScratchPad | id=`3a4d2189-88b1-47ea-bb49-8596136ac621` | sources=0 | updated=2024-07-23T13:41:10Z
- 84. Briefing on GoT | id=`19b10f84-73fa-4ea4-863c-a498eb7b52d9` | sources=1 | updated=2024-07-02T23:05:59Z
- 85. RAG | id=`72ee9178-e2fe-422e-a1c3-059e5f675ec6` | sources=3 | updated=2024-05-16T01:20:11Z
- 86. Canadian Death Obligations | id=`ad71293d-f4b5-4efd-96ab-ea0a9edb1a07` | sources=1 | updated=2024-04-07T21:47:59Z
- 87. Introduction to NotebookLM | id=`f7607d7a-584c-4f35-96fc-f6815c573a6c` | sources=27 | updated=2023-12-30T17:56:36Z

## Output Files
- `final_response.md` (this file)
- `setup_actions.log`
- `mcp_attempt.txt`
- `nlm_login_check_initial.txt`, `nlm_login_check_initial.exit`
- `auth_preflight.json`, `auth_preflight.exit` (initial failed import attempt before PYTHONPATH fix)
- `nlm_login_check_post_setup.txt`, `nlm_login_check_post_setup.exit`
- `auth_preflight_post_setup.json`, `auth_preflight_post_setup.exit`
- `notebooks_cli_post_setup.json`, `notebooks_cli_post_setup.err`, `notebooks_cli_post_setup.exit`
- `notebooks_cli_post_setup.txt`, `notebooks_cli_post_setup_txt.err`, `notebooks_cli_post_setup_txt.exit`
