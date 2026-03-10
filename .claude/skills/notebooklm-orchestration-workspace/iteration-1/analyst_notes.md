# Analyst Notes (Iteration 1)

## Key outcomes
- `with_skill` achieved 100% expectation pass rate across all 3 evals.
- `without_skill` averaged 66.7% pass rate.
- Largest separation came from structured contract consistency (`status` + `path_used`) and explicit operational reporting.

## Signals worth watching
- Eval 3 (confirmation gate) is weakly discriminating for safety behavior because both configurations correctly avoided mutation before confirmation.
- Eval 1 baseline output was very verbose and less structured; this inflated token usage and hurt precision of assertion matching.

## Metric caveats
- Timing and token telemetry from subagent completion notifications was not available in this environment.
- `time_seconds` is placeholder `0.0` for all runs.
- Token counts in benchmark are using `output_chars` fallback and should be treated as rough proxy, not true model token usage.

## Suggested iteration-2 improvements
1. Add one assertion for explicit Infisical-secret handling language in auth recovery flows.
2. Add one assertion requiring `next_step_if_blocked` for blocked/needs_confirmation states.
3. Add a CLI-auth-failure eval to verify seed flow behavior when `vps` auth is expired.
