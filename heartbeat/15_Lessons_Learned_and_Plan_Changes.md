---
title: "Lessons Learned + Plan Changes"
status: living
last_updated: 2026-01-29
---

# 15. Lessons Learned + Plan Changes (Heartbeat Project)

Use this document to record **deviations**, **issues**, and **lessons learned** while executing the phased plan. Keep entries short and dated.

## 1. Change log (plan deviations)
| Date | Phase | Change | Rationale | Impact |
|------|-------|--------|-----------|--------|
| 2026-01-29 | — | Initialized document | Tracking requested | — |

## 2. Lessons learned
| Date | Area | Lesson | Action taken |
|------|------|--------|--------------|
| 2026-01-29 | — | Initialized document | — |

## 3. Issues encountered
| Date | Area | Issue | Resolution |
|------|------|-------|------------|
| 2026-01-29 | Testing | `tests/stabilization/test_smoke_direct.py` timed out (30s) running `python -m universal_agent.main` | Pending: re-run with longer timeout and investigate CLI startup latency |
| 2026-01-29 | Testing | agent-browser failed: missing Chromium (`chrome-headless-shell`) after `agent-browser install` | Pending: run `npx playwright install chromium` and retry parity workflow |

## 4. Open questions (live)
- (Add questions that block progress or require decision.)

## 5. References
- Implementation plan tracker: @/home/kjdragan/lrepos/universal_agent/heartbeat/10_Implementation_Plan.md#1-214
