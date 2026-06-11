"""Pin the operating-hours dormancy default for system cron jobs.

Policy: cron schedules registered in `gateway_server.py:_ensure_*_cron_job`
must fall inside the active window (6 AM – 10 PM Houston, America/Chicago)
unless they're documented as an exception in
`docs/operations/operating_hours_dormancy.md`.

This guard catches the failure mode where a future commit registers a
3 AM cron without realising it violates the dormancy default. It does NOT
walk Python AST or import the gateway_server module (too heavy + has
side-effects); instead it does string-grep against the source file,
which is good enough for catching the static `default_cron=` literals.

The active window in cron-hour terms (Chicago TZ): hours 6, 7, 8, ...,
21 are active. Hours 22, 23, 0, 1, 2, 3, 4, 5 are dormant.
"""
from __future__ import annotations

from pathlib import Path
import re

from universal_agent.services import dormancy

GATEWAY_SERVER = Path("src/universal_agent/gateway_server.py")
DOC_NIGHTLY = Path(".github/workflows/doc-nightly.yml")
DORMANCY_DOC = Path("docs/operations/operating_hours_dormancy.md")
POST_MERGE_DEPLOY = Path(".github/workflows/post-merge-deploy.yml")
CI_FAILURE_ISSUE = Path(".github/workflows/ci-failure-issue.yml")

# INTERVAL crons that legitimately run 24/7 — exempt from the active-window
# check below. Only repeating/interval crons (``*/N`` or hourly ranges) are
# subject to that check; a FIXED-TIME cron runs at its chosen time and does NOT
# belong here (e.g. nightly_wiki at 3:15 AM is allowed by the fixed-time
# soft-warn test, not by this list). Adding an interval here also needs a row in
# the canonical doc (project_docs/08_operations/03_dormancy_and_operating_hours.md).
DOCUMENTED_EXCEPTIONS = {
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
# 10 PM cutoff (last tick at 9:30 PM is fine; 22:00 itself is the start of
# dormancy). So active hours are [6, 7, 8, ..., 21]. Derived from the single
# source of truth in services/dormancy.py so this guard tracks the constants
# rather than re-hardcoding the window.
ACTIVE_HOURS = set(range(dormancy.ACTIVE_START_HOUR, dormancy.ACTIVE_END_HOUR))


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


def _is_interval_cron(cron_expr: str) -> bool:
    """True if a cron REPEATS sub-daily (fires across many hours), so the
    dormancy window is a meaningful constraint; False for a FIXED-TIME cron
    (one or a few discrete times a day), which runs as scheduled.

    Interval signals: the minute fires multiple times per hour (``*`` or
    ``*/N``), or the hour field spans (``*``, ``*/N``, or a contiguous ``A-B``
    range — "every hour in the window"). A discrete hour list like ``10,15`` is
    fixed-time (two deliberately chosen hours), NOT an interval.

    Rationale: the accidental-overnight footgun is an interval like
    ``*/30 * * * *`` that someone forgot to window — it quietly fires all night.
    A fixed-time overnight schedule (``15 3 * * *``) is deliberate, not a slip,
    so it runs as scheduled.
    """
    fields = cron_expr.split()
    if len(fields) != 5:
        return False  # malformed — the dedicated checks below surface it
    minute, hour = fields[0], fields[1]
    if "*" in minute or "/" in minute:
        return True
    if "*" in hour or "/" in hour or "-" in hour:
        return True
    return False


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
    assert "6:00 AM" in body and "10:00 PM" in body, (
        "Dormancy doc must state the active window in plain English"
    )


def test_claude_md_links_to_dormancy_doc() -> None:
    """CLAUDE.md's Working Rules section must reference the policy.

    Future agents read CLAUDE.md before touching anything; the dormancy
    rule MUST be visible there or it'll get violated routinely.
    """
    body = Path("CLAUDE.md").read_text(encoding="utf-8")
    # Canonical dormancy doc moved to project_docs/ in the 2026-05-29 doc rebuild;
    # CLAUDE.md links the rebuilt doc now (this guards the link, not the old path).
    assert "03_dormancy_and_operating_hours.md" in body
    assert "6:00 AM" in body and "10:00 PM" in body
    assert "Houston" in body


def test_interval_crons_respect_dormancy_window_or_documented_exception() -> None:
    """INTERVAL crons must stay inside the active window — or be exempt.

    Dormancy windowing only meaningfully applies to repeating/interval crons
    (``*/N`` or hourly ranges) — those are the ones that, if mis-scheduled,
    quietly fire all night and leak quota/email. Each interval cron's hours
    must fall in 6 AM – 9 PM Houston, UNLESS the job is in DOCUMENTED_EXCEPTIONS
    (an interval that legitimately runs 24/7) or carries the per-job
    ``UA_<JOB>_24_7`` runtime opt-out (schedule widened to 24/7, gated at
    runtime — those timers/scripts are guarded in
    ``test_dormancy_schedule_consistency.py``). Fixed-time crons are handled by
    ``test_fixed_time_crons_run_as_scheduled`` below.
    """
    content = GATEWAY_SERVER.read_text(encoding="utf-8")
    registrations = _extract_cron_registrations(content)
    assert registrations, (
        "Found no cron registrations — regex broke or registrations were removed"
    )

    violations: list[str] = []
    for job, cron, tz in registrations:
        if not _is_interval_cron(cron):
            continue  # fixed-time crons run as scheduled (soft-warn test)
        if job in DOCUMENTED_EXCEPTIONS:
            continue
        try:
            hours = _hours_used_by_cron(cron)
        except ValueError as exc:
            violations.append(f"{job}: malformed cron {cron!r}: {exc}")
            continue
        if not hours.issubset(ACTIVE_HOURS):
            offending = sorted(hours - ACTIVE_HOURS)
            violations.append(
                f"{job}: interval cron={cron!r} tz={tz!r} fires in dormant "
                f"hour(s) {offending}. Window the schedule into 6-21, OR add the "
                f"job to DOCUMENTED_EXCEPTIONS (with a row in {DORMANCY_DOC}) if "
                f"it must run 24/7, OR give it the per-job UA_<JOB>_24_7 runtime "
                f"opt-out (widened timer + should_run gate)."
            )

    assert not violations, (
        "Interval cron(s) violate the operating-hours dormancy window:\n  - "
        + "\n  - ".join(violations)
    )


def test_fixed_time_crons_run_as_scheduled() -> None:
    """FIXED-TIME crons run at their chosen time — dormancy does not apply.

    A cron pinned to one or a few discrete times (e.g. ``5 7 * * *`` or
    ``15 3 * * *``) runs as scheduled; "24/7 vs windowed" is meaningless for it.
    A deliberately-overnight time (``nightly_wiki`` at 3:15 AM, feeding the
    6:30 AM briefing) is allowed. We emit an informational notice — never a
    failure — if a fixed-time cron lands in the dormant hours, so a deliberate
    overnight schedule stays visible without being blocked.
    """
    content = GATEWAY_SERVER.read_text(encoding="utf-8")
    registrations = _extract_cron_registrations(content)

    notices: list[str] = []
    for job, cron, tz in registrations:
        if _is_interval_cron(cron):
            continue
        try:
            hours = _hours_used_by_cron(cron)
        except ValueError:
            continue  # malformed fixed-time cron is not this test's concern
        if not hours.issubset(ACTIVE_HOURS):
            notices.append(
                f"{job}: {cron!r} fires at dormant hour(s) "
                f"{sorted(hours - ACTIVE_HOURS)} — allowed (fixed-time runs as scheduled)"
            )

    if notices:
        print("\n[dormancy] fixed-time crons scheduled in dormant hours (allowed, FYI):")
        for notice in notices:
            print("  -", notice)
    # No assertion: fixed-time crons run as scheduled by policy.


def test_hackernews_snapshot_uses_active_hour_range() -> None:
    """Pin the specific schedule for hackernews_snapshot.

    Catches a regression where someone reverts to `*/30 * * * *` (24/7).
    """
    content = GATEWAY_SERVER.read_text(encoding="utf-8")
    expected_cron = f'default_cron="0,30 {dormancy.cron_hour_field()} * * *"'
    assert (
        expected_cron in content
        and '"hackernews_snapshot"' in content
    ), (
        "hackernews_snapshot must default to '0,30 6-21 * * *' America/Chicago "
        "(2026-05-19 dormancy widened to 6 AM–10 PM Houston). Pre-2026-05-10 "
        "it was '*/30 * * * *' UTC; 2026-05-10 through 2026-05-19 it was "
        "'0,30 6-20 * * *'. If you need to change it, update "
        "docs/operations/operating_hours_dormancy.md."
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


def test_doc_nightly_runs_overnight() -> None:
    """The doc-drift sweep schedule must stay in the CT overnight window (UTC).

    Operator decision 2026-06-10 (ADR 11 §10): this job is a DOCUMENTED EXCEPTION
    to the active-window default. It feeds the dark-factory doc-triage loop on the
    always-on desktop — the sweep fires ~1:35 AM CT, the loop triages/fixes on idle
    Max quota, and doc fixes are merged before morning. The dormancy rationale
    ("quota burn nobody reads until morning") does not apply: the overnight loop IS
    the consumer. Until 2026-06-10 this test enforced the opposite (active window
    12-01 UTC), which is why the sweep sat at 18:35 UTC ≈ 1:35 PM CT.
    """
    body = DOC_NIGHTLY.read_text(encoding="utf-8")
    # Find the cron schedule
    m = re.search(r"-\s*cron:\s*'([^']+)'", body)
    assert m, f"Could not find cron schedule in {DOC_NIGHTLY}"
    cron = m.group(1)
    hours = _hours_used_by_cron(cron)
    # GHA schedules are UTC. CT-overnight (10 PM-6 AM) = hours 03-10 UTC in CDT,
    # 04-11 UTC in CST; the DST-safe intersection is hours 04-10 UTC.
    safe_utc_overnight = set(range(4, 11))
    assert hours.issubset(safe_utc_overnight), (
        f"doc-nightly cron={cron!r} hits UTC hour(s) "
        f"{sorted(hours - safe_utc_overnight)} outside the safe DST-overlap "
        f"CT-overnight window 04-10 UTC. The dark factory expects this sweep "
        f"overnight (operator decision 2026-06-10, ADR 11 §10); use a UTC hour in 4-10."
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
