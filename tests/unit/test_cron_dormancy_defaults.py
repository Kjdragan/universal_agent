"""Pin the operating-hours dormancy default for system cron jobs.

Policy: cron schedules registered in `gateway_server.py:_ensure_*_cron_job`
must fall inside the active window (6 AM – 9 PM Houston, America/Chicago)
unless they're documented as an exception in
`docs/operations/operating_hours_dormancy.md`.

This guard catches the failure mode where a future commit registers a
3 AM cron without realising it violates the dormancy default. It does NOT
walk Python AST or import the gateway_server module (too heavy + has
side-effects); instead it does string-grep against the source file,
which is good enough for catching the static `default_cron=` literals.

The active window in cron-hour terms (Chicago TZ): hours 6, 7, 8, ...,
20 are active. Hours 21, 22, 23, 0, 1, 2, 3, 4, 5 are dormant.
"""
from __future__ import annotations

from pathlib import Path
import re

GATEWAY_SERVER = Path("src/universal_agent/gateway_server.py")
NIGHTLY_DOC_DRIFT = Path(".github/workflows/nightly-doc-drift-audit.yml")
OPENCLAW_SYNC = Path(".github/workflows/openclaw-release-sync.yml")
DORMANCY_DOC = Path("docs/operations/operating_hours_dormancy.md")
POST_MERGE_DEPLOY = Path(".github/workflows/post-merge-deploy.yml")
CI_FAILURE_ISSUE = Path(".github/workflows/ci-failure-issue.yml")

# Documented exceptions (services that genuinely run inside the dormancy
# window per the exception checklist in operating_hours_dormancy.md).
# Adding to this list is the correct way to bypass the test for a new
# 24/7 service — but you also need to add a row to the exceptions
# section of the operating_hours_dormancy.md doc.
DOCUMENTED_EXCEPTIONS = {
    "nightly_wiki",  # 3:15 AM Houston — feeds 6:30 AM morning briefing
    # Hermes Phase C (PR #221): every-60s dispatcher for tasks tagged
    # metadata.preferred_vp = "vp.general.primary". Default OFF via
    # UA_ATLAS_DIRECT_DISPATCH_ENABLED=0. Exception #3 (latency-sensitive):
    # Atlas-eligible tasks must dispatch within ~60s of being queued, not
    # wait until 6 AM. See operating_hours_dormancy.md exceptions table.
    "atlas_direct_dispatch",
    # Simone-chat mission control (PR #255): every-60s SQLite-only
    # housekeeping that promotes simone_chat Task Hub rows from
    # status="in_progress" to status="completed" once Simone has proposed
    # completion and the operator has been silent for UA_SIMONE_CHAT_IDLE_MINUTES
    # (default 10). No LLM tokens, no external API. Exception #3
    # (latency-sensitive operator-facing state): a chat started at 8:55 PM
    # crosses into the dormant window mid-cycle; without 24/7 the row stays
    # "in_progress" overnight and pollutes the dashboard. See
    # operating_hours_dormancy.md exceptions table.
    "simone_chat_auto_complete",
}

# Hours considered active in America/Chicago. 6 AM start (operator wakes),
# 9 PM cutoff (last tick at 8:30 PM is fine; 21:00 itself is the start of
# dormancy). So active hours are [6, 7, 8, ..., 20].
ACTIVE_HOURS = set(range(6, 21))


def _extract_cron_registrations(content: str) -> list[tuple[str, str, str]]:
    """Pull (system_job, default_cron, default_timezone) tuples from gateway_server.py.

    Matches the pattern used by _register_system_cron_job calls. Brittle to
    formatting (multi-line dict literals with the three keys nearby) but
    that's acceptable — every existing call uses the same shape and our
    own ai_coder_instructions encourages consistency.
    """
    pattern = re.compile(
        r'system_job="(?P<job>[^"]+)"\s*,'
        r'(?:\s*#[^\n]*\n)*'  # tolerate inline comments between keys
        r'\s*default_cron="(?P<cron>[^"]+)"\s*,'
        r'(?:\s*#[^\n]*\n)*'
        r'\s*default_timezone="(?P<tz>[^"]+)"',
        re.DOTALL,
    )
    return [
        (m.group("job"), m.group("cron"), m.group("tz"))
        for m in pattern.finditer(content)
    ]


def _hours_used_by_cron(cron_expr: str) -> set[int]:
    """Return the set of hour values activated by a 5-field cron expression.

    Handles `*`, `H`, `H1,H2,H3`, `H1-H2`, `H1-H2/N`, `*/N` for the hour field.
    """
    fields = cron_expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 cron fields, got {len(fields)}: {cron_expr!r}")
    hour_field = fields[1]
    if hour_field == "*":
        return set(range(24))
    out: set[int] = set()
    for part in hour_field.split(","):
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if range_part in ("*", ""):
                start, end = 0, 23
            elif "-" in range_part:
                start_s, end_s = range_part.split("-", 1)
                start, end = int(start_s), int(end_s)
            else:
                start = end = int(range_part)
            out.update(h for h in range(start, end + 1) if (h - start) % step == 0)
        elif "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            out.update(range(start, end + 1))
        else:
            out.add(int(part))
    return out


# ─── tests ─────────────────────────────────────────────────────────────


def test_dormancy_doc_exists() -> None:
    """The canonical doc explaining the dormancy default must exist.

    If this fails, someone deleted the doc — either restore it or
    update CLAUDE.md to point somewhere else.
    """
    assert DORMANCY_DOC.exists(), (
        f"Missing {DORMANCY_DOC} — the dormancy default policy needs a "
        f"canonical doc. CLAUDE.md links to it."
    )
    body = DORMANCY_DOC.read_text(encoding="utf-8")
    assert "6:00 AM" in body and "9:00 PM" in body, (
        "Dormancy doc must state the active window in plain English"
    )


def test_claude_md_links_to_dormancy_doc() -> None:
    """CLAUDE.md's Working Rules section must reference the policy.

    Future agents read CLAUDE.md before touching anything; the dormancy
    rule MUST be visible there or it'll get violated routinely.
    """
    body = Path("CLAUDE.md").read_text(encoding="utf-8")
    assert "operating_hours_dormancy.md" in body
    assert "6:00 AM" in body and "9:00 PM" in body
    assert "Houston" in body


def test_internal_crons_default_to_active_hours_or_documented_exception() -> None:
    """Every cron in gateway_server.py must run during 6 AM – 9 PM Houston
    OR be in DOCUMENTED_EXCEPTIONS (and listed in the dormancy doc)."""
    content = GATEWAY_SERVER.read_text(encoding="utf-8")
    registrations = _extract_cron_registrations(content)
    assert registrations, (
        "Found no cron registrations — regex broke or registrations were removed"
    )

    violations: list[str] = []
    for job, cron, tz in registrations:
        if job in DOCUMENTED_EXCEPTIONS:
            continue
        # We only validate hour-of-day. America/Chicago + UTC are both
        # acceptable timezones; we check that the cron's hours fall in
        # ACTIVE_HOURS regardless of which TZ name is used. (For UTC-
        # registered crons we still want hours in 6-20 because that
        # range USED IN UTC corresponds to ~midnight–3 PM CDT, which is
        # a wide enough swath that the 24/7 case is the only meaningful
        # violation. The strict check happens below.)
        try:
            hours = _hours_used_by_cron(cron)
        except ValueError as exc:
            violations.append(f"{job}: malformed cron {cron!r}: {exc}")
            continue
        if not hours.issubset(ACTIVE_HOURS):
            offending = sorted(hours - ACTIVE_HOURS)
            violations.append(
                f"{job}: cron={cron!r} tz={tz!r} hits hour(s) {offending} "
                f"outside the active window 6-20. Either schedule inside "
                f"the active window OR add to DOCUMENTED_EXCEPTIONS in this "
                f"test AND add an exception row in {DORMANCY_DOC}."
            )

    assert not violations, (
        "Cron jobs violate the operating-hours dormancy default:\n  - "
        + "\n  - ".join(violations)
    )


def test_hackernews_snapshot_uses_active_hour_range() -> None:
    """Pin the specific schedule for hackernews_snapshot.

    Catches a regression where someone reverts to `*/30 * * * *` (24/7).
    """
    content = GATEWAY_SERVER.read_text(encoding="utf-8")
    assert (
        'default_cron="0,30 6-20 * * *"' in content
        and '"hackernews_snapshot"' in content
    ), (
        "hackernews_snapshot must default to '0,30 6-20 * * *' America/Chicago "
        "(2026-05-10 dormancy default). Pre-2026-05-10 it was '*/30 * * * *' UTC; "
        "if you need to change it, update docs/operations/operating_hours_dormancy.md."
    )


def test_vp_coder_workspace_pruning_moved_to_active_hours() -> None:
    """vp_coder_workspace_pruning runs Sunday afternoon.

    2026-05-10: moved from 4 AM Sun → 7 AM Sun (dormancy default).
    2026-05-11: moved from 7 AM Sun → 5:05 PM Sun as part of the
    cron-spread refactor. 7 AM Sunday was bunching with
    proactive_report_morning at 7:05 AM.
    """
    content = GATEWAY_SERVER.read_text(encoding="utf-8")
    assert (
        'default_cron="5 17 * * 0"' in content
        and '"vp_coder_workspace_pruning"' in content
    ), (
        "vp_coder_workspace_pruning must default to '5 17 * * 0' America/Chicago "
        "(Sunday 5:05 PM Houston). 2026-05-11 spread refactor moved it from "
        "7 AM Sun (which collided with proactive_report_morning) to 5:05 PM."
    )


def test_nightly_doc_drift_audit_runs_in_active_hours() -> None:
    """GitHub Actions schedule must be inside the active window (UTC)."""
    body = NIGHTLY_DOC_DRIFT.read_text(encoding="utf-8")
    # Find the cron schedule
    m = re.search(r"-\s*cron:\s*'([^']+)'", body)
    assert m, f"Could not find cron schedule in {NIGHTLY_DOC_DRIFT}"
    cron = m.group(1)
    hours = _hours_used_by_cron(cron)
    # GHA schedules are UTC. CDT active = 11-01 UTC, CST active = 12-02 UTC.
    # Pick a conservative window where both DSTs overlap: 12-01 UTC (the
    # intersection of CDT-active and CST-active).
    safe_utc_active = set(range(12, 24)) | {0, 1}
    assert hours.issubset(safe_utc_active), (
        f"nightly-doc-drift-audit cron={cron!r} hits UTC hour(s) "
        f"{sorted(hours - safe_utc_active)} outside the safe DST-overlap "
        f"active window 12-01 UTC. Use a UTC hour in 12-23 or 00-01."
    )


def test_openclaw_release_sync_runs_in_active_hours() -> None:
    """Same check for the openclaw-release-sync workflow."""
    body = OPENCLAW_SYNC.read_text(encoding="utf-8")
    m = re.search(r"-\s*cron:\s*'([^']+)'", body)
    assert m, f"Could not find cron schedule in {OPENCLAW_SYNC}"
    cron = m.group(1)
    hours = _hours_used_by_cron(cron)
    safe_utc_active = set(range(12, 24)) | {0, 1}
    assert hours.issubset(safe_utc_active), (
        f"openclaw-release-sync cron={cron!r} hits UTC hour(s) "
        f"{sorted(hours - safe_utc_active)} outside the safe DST-overlap "
        f"active window 12-01 UTC."
    )


# ─── infrastructure-event-handler tripwires ───────────────────────────
# These are NOT subject to dormancy (they handle real-world events whose
# timing is uncontrollable — merges, PR failures, deploys). See
# docs/operations/operating_hours_dormancy.md § "Scope". Tests here just
# pin that the files exist so a future cleanup can't silently remove them
# and re-introduce the gaps that PR #224 / this PR closed.


def test_pr_auto_merge_uses_pat_to_avoid_token_suppression() -> None:
    """pr-auto-merge.yml must use AUTO_MERGE_PAT (not GITHUB_TOKEN) so the
    downstream squash-merge `push` event fires `deploy.yml`.

    Replaces the prior `test_post_merge_deploy_workflow_exists` (which
    asserted the existence of a redundant bridge workflow). That bridge
    was the workaround for GitHub's GITHUB_TOKEN-downstream-trigger
    suppression rule. With pr-auto-merge.yml using a fine-grained PAT
    (PR #232, 2026-05-11), the bridge is no longer needed and the
    workflow was deleted (PR replacing this test).

    If this test fails, the auto-merge → deploy chain is back to being
    GITHUB_TOKEN-driven and production will silently stop deploying on
    merges. Fix is to restore the PAT reference in pr-auto-merge.yml.
    """
    pr_auto_merge = Path(".github/workflows/pr-auto-merge.yml")
    assert pr_auto_merge.exists(), (
        f"Missing {pr_auto_merge} — without it, claude/* PRs don't get "
        f"auto-merge enabled. Restore the file."
    )
    body = pr_auto_merge.read_text(encoding="utf-8")
    assert "AUTO_MERGE_PAT" in body, (
        "pr-auto-merge.yml must reference secrets.AUTO_MERGE_PAT so the "
        "downstream push to main fires deploy.yml. Reverting to "
        "GITHUB_TOKEN-only will silently break the deploy-on-merge chain "
        "per GitHub's downstream-trigger suppression rule."
    )


def test_post_merge_deploy_workflow_removed() -> None:
    """The bridge workflow file (post-merge-deploy.yml) must stay deleted.

    The PAT-based pr-auto-merge.yml is the canonical mechanism for
    firing deploy.yml on merge. Re-introducing the bridge workflow
    would cause two Deploy runs per merge (one from the natural push
    trigger, one from the bridge's workflow_dispatch), doubling
    Actions cost and cluttering the Actions tab. If the bridge needs
    to come back as a backstop, route it through a single deploy via
    the concurrency guard, not parallel.
    """
    assert not POST_MERGE_DEPLOY.exists(), (
        f"{POST_MERGE_DEPLOY} re-appeared. It was deleted because the "
        f"PAT-based pr-auto-merge.yml makes it redundant — every merge "
        f"would otherwise fire two Deploy runs (push + workflow_dispatch). "
        f"If you need a bridge, make sure it doesn't double-deploy."
    )


def test_ci_failure_issue_filer_workflow_exists() -> None:
    """Workflow that opens an issue on failed workflow runs.

    Surfaces silent CI failures (like PR #221's Validate failure that sat
    unnoticed) so autonomous AI sessions and the operator both discover
    them immediately. Added 2026-05-11.
    """
    assert CI_FAILURE_ISSUE.exists(), (
        f"Missing {CI_FAILURE_ISSUE} — without it, failed workflow runs "
        f"can sit unnoticed for hours. Restore the file or replace with "
        f"a different notification mechanism."
    )
    body = CI_FAILURE_ISSUE.read_text(encoding="utf-8")
    assert "workflow_run" in body
    assert "conclusion == 'failure'" in body, (
        "ci-failure-issue.yml must gate on conclusion=='failure'; otherwise "
        "it spams issues on every successful run."
    )
