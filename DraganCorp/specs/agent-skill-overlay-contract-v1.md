# Agent and Skill Overlay Contract v1

Defines how mission-specific factory clones customize behavior while preserving one shared UA skeleton.

## 1) Goals

1. Enable fast clone specialization without hard forking.
2. Keep compatibility with core runtime and deployment tooling.
3. Ensure overlays are explicit, reviewable, and reversible.

## 2) Overlay object

```yaml
overlay_id: freelance_research_v1
base_profile: ua_core_v1
agents:
  enable:
    - research-specialist
    - report-writer
    - action-coordinator
  disable:
    - video-creation-expert
skills:
  enable:
    - summarize
    - reddit-intel
  disable:
    - manim_skill
prompt_overlays:
  identity: "factory_research_identity_v1"
  policy: "factory_research_policy_v1"
limits:
  max_runtime_minutes: 720
  max_parallel_missions: 2
  approval_required:
    - destructive_file_actions
    - external_messaging
observability:
  heartbeat_interval_seconds: 300
  mission_progress_interval_seconds: 900
```

## 3) Validation rules

1. Base profile must exist and be versioned.
2. Every enabled/disabled agent and skill must resolve to known IDs.
3. Overlay cannot remove mandatory safety policies.
4. Limits must be within global control-plane caps.

## 4) Promotion rules

An overlay graduates to reusable template only after:

1. successful pilot mission completion,
2. no critical incidents,
3. acceptable maintenance burden,
4. explicit architecture review sign-off.

## 5) Rollback behavior

If validation fails or incidents spike:

1. disable overlay,
2. revert to base profile,
3. route pending missions back to Simone/in-core lane.
