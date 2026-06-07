"""Backlog triage — assess the open proactive backlog and email a Simone digest.

Reads the open ``skill-gap`` / ``deslop-findings`` GitHub issues plus recently
actioned skill/deslop PRs, asks the ZAI/GLM proxy to produce an ASSESSMENT of
what's going on + RANKED recommended actions + a single copy-pasteable
"recommended next run" brief an agent could execute, then emails it "from Simone"
(the AgentMail ``simone`` inbox).

Design notes:
  * Read-only over GitHub (``gh`` CLI); never mutates issues/PRs/code.
  * LLM via the ZAI/GLM proxy (``resolve_sonnet`` -> glm-5-turbo) — no Anthropic
    spend; heavy imports are lazy so ``--dry-run`` needs only stdlib + ``gh``.
  * ALWAYS exits 0 (advisory). No creds / no data -> prints a note, exits 0.

Usage:
    python -m universal_agent.backlog_triage --dry-run     # compose + print, no email
    python -m universal_agent.backlog_triage --send        # compose + email from Simone

When run on the gateway as a cron with ``metadata.notify_on_artifact == true``,
the existing reminder sweep re-surfaces the email until it is acked. Standalone
(``--send``) sends a single immediate email.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))  # so universal_agent imports resolve as a script

REPO = os.getenv("UA_TRIAGE_REPO", "Kjdragan/universal_agent")
LABELS = ("skill-gap", "deslop-findings")
PR_SEARCH_LIMIT = int(os.getenv("UA_TRIAGE_PR_LIMIT", "20"))

SYSTEM = (
    "You are a backlog-triage analyst for the Universal Agent project. You are given the "
    "open proactive backlog (GitHub issues labelled skill-gap / deslop-findings, plus a "
    "planning handoff) and the recently-merged skill/deslop PRs that show what has already "
    "been actioned. Produce a crisp operator-facing readout. Be specific and cite issue/PR "
    "numbers. Distinguish what is SHIPPED vs OPEN vs STALE/needs-a-decision. End with ONE "
    "copy-pasteable 'recommended next run' brief that an agent (or Cody mission) could execute "
    "to act on the most valuable open item. Return ONLY a JSON object: "
    '{"headline":"<one line>", "assessment":"<2-5 sentence prose>", '
    '"actions":[{"item":"<short>","why":"<short>","priority":"P1|P2|P3"}], '
    '"next_run_brief":"<a concrete, self-contained instruction an agent could run>"}.'
)


def _gh_json(args: list[str]) -> list | dict | None:
    try:
        out = subprocess.run(
            ["gh", *args], check=True, capture_output=True, text=True, cwd=str(REPO_ROOT)
        ).stdout.strip()
        return json.loads(out) if out else []
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] gh {' '.join(args)} failed: {exc}", file=sys.stderr)
        return None


def _gather() -> dict:
    """Read-only snapshot of the backlog the triage assesses."""
    data: dict = {"repo": REPO, "open_issues": {}, "recent_actioned_prs": []}
    for label in LABELS:
        rows = _gh_json([
            "issue", "list", "--repo", REPO, "--state", "open", "--label", label,
            "--json", "number,title,updatedAt,url",
        ]) or []
        data["open_issues"][label] = rows
    # planning handoff(s)
    data["open_issues"]["planning"] = _gh_json([
        "issue", "list", "--repo", REPO, "--state", "open", "--label", "planning",
        "--json", "number,title,url",
    ]) or []
    # recently-merged skill/deslop PRs = disposition signal (what was actioned)
    prs = _gh_json([
        "pr", "list", "--repo", REPO, "--state", "merged", "--search", "skill OR deslop",
        "--limit", str(PR_SEARCH_LIMIT), "--json", "number,title,mergedAt",
    ]) or []
    data["recent_actioned_prs"] = prs
    return data


def _load_zai_env() -> None:
    if os.getenv("ANTHROPIC_BASE_URL") and (
        os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    ):
        return
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Infisical/ZAI bootstrap skipped: {exc}", file=sys.stderr)


def _client():
    from anthropic import Anthropic  # lazy

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        return None
    kwargs = {"api_key": api_key}
    if os.getenv("ANTHROPIC_BASE_URL"):
        kwargs["base_url"] = os.getenv("ANTHROPIC_BASE_URL")
    return Anthropic(**kwargs)


def _resolve_model(override: str = "") -> str:
    if override:
        return override
    try:
        from universal_agent.utils.model_resolution import resolve_sonnet

        return resolve_sonnet()
    except Exception:  # noqa: BLE001
        return "glm-5-turbo"


def _synthesize(data: dict, model_override: str = "") -> dict | None:
    _load_zai_env()
    client = _client()
    if client is None:
        return None
    model = _resolve_model(model_override)
    user = (
        "Backlog snapshot (JSON):\n"
        + json.dumps(data, indent=2)[:18000]
        + "\n\nProduce the triage JSON now."
    )
    try:
        resp = client.messages.create(
            model=model, max_tokens=1500, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.lstrip().lower().startswith("json"):
                raw = raw.lstrip()[4:]
            raw = raw.strip().strip("`").strip()
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] synthesis failed: {exc}", file=sys.stderr)
        return None


def _deterministic(data: dict) -> dict:
    """Fallback assessment if the LLM is unavailable — pure counts, no judgement."""
    sg = len(data["open_issues"].get("skill-gap", []))
    ds = len(data["open_issues"].get("deslop-findings", []))
    pr = len(data["recent_actioned_prs"])
    return {
        "headline": f"Backlog: {sg} skill-gap + {ds} deslop-findings open; {pr} recently actioned PRs.",
        "assessment": (
            f"{sg} open skill-gap issue(s), {ds} open deslop-findings issue(s). "
            f"{pr} skill/deslop PRs merged recently (disposition signal). "
            "LLM synthesis unavailable — counts only."
        ),
        "actions": (
            [{"item": f"Triage deslop-findings #{i['number']}", "why": "open, unactioned",
              "priority": "P2"} for i in data["open_issues"].get("deslop-findings", [])]
            or [{"item": "No open backlog items", "why": "queue empty", "priority": "P3"}]
        ),
        "next_run_brief": "Review the open deslop-findings issues and decide apply-or-dismiss for each.",
    }


def _markdown(t: dict, data: dict) -> str:
    lines = [
        "## 🗂️ Backlog triage — assessment & guidance",
        "",
        f"**{t.get('headline', 'Backlog triage')}**",
        "",
        t.get("assessment", ""),
        "",
        "### Recommended actions",
    ]
    for a in t.get("actions", []) or []:
        lines.append(f"- **[{a.get('priority', 'P3')}]** {a.get('item', '')} — _{a.get('why', '')}_")
    nb = t.get("next_run_brief", "")
    if nb:
        lines += ["", "### ▶️ Recommended next run (hand to an agent / Cody mission)", "", "```", nb, "```"]
    open_refs = []
    for label, rows in data["open_issues"].items():
        for r in rows or []:
            open_refs.append(f"- {label}: #{r['number']} {r.get('title', '')}")
    if open_refs:
        lines += ["", "### Open backlog (source)", *open_refs]
    lines += [
        "",
        "---",
        "_Advisory readout — nothing here was changed automatically. Delivered by Simone "
        "(Universal Agent backlog triage). Reply or ack to stop the reminders._",
    ]
    return "\n".join(lines)


def _recipient(override: str = "") -> str:
    # Priority address is the gmail (the general UA_*_EMAIL chain points at a
    # bounced outlook addr). Centralized in simone_mail.resolve_recipient.
    from universal_agent.simone_mail import resolve_recipient

    return resolve_recipient(override)


def _send_email(subject: str, text: str, to: str = "") -> dict:
    """Send the digest from the Simone inbox via the shared helper. Never raises."""
    from universal_agent.simone_mail import send_simone_email

    res = send_simone_email(to=to, subject=subject, text=text, source="backlog-triage")
    if res.get("status") == "sent":
        print(f"[sent] to={res.get('to')} inbox={res.get('inbox')} message_id={res.get('message_id')}")
    else:
        print(f"[warn] send {res.get('status')}: {res.get('reason', '')}", file=sys.stderr)
    return res


def _seed_reminders(data: dict, triage: dict, recipient: str, message_id: str = "") -> None:
    """Hybrid layer: seed/refresh ONE reminder row so the existing
    cron_artifact_reminders sweep fires the intra-day +4h/+72h nudges.

    Best-effort and never raises. Seeds the cadence only on first insert / re-arm
    (upsert overwrites metadata_json, so on an active row we MUST pass the existing
    metadata back to avoid resetting the clock). When the actionable backlog is
    empty, the row is marked accepted to stop the sweep. Targets whatever
    activity_state.db this host resolves (desktop run -> desktop DB + desktop sweep).
    """
    import time

    try:
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
        from universal_agent.services import proactive_artifacts as pa
        from universal_agent.services.cron_artifact_notifier import _seed_reminder_state
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] reminder-seed unavailable: {exc}", file=sys.stderr)
        return

    open_actionable = (data["open_issues"].get("skill-gap") or []) + (
        data["open_issues"].get("deslop-findings") or []
    )
    art_id = "triage-backlog-" + REPO.replace("/", "-")
    headline = triage.get("headline", "Open proactive backlog")
    summary = (triage.get("assessment", "") or "")[:1000]
    try:
        conn = connect_runtime_db(get_activity_db_path())
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] activity DB open failed: {exc}", file=sys.stderr)
        return
    try:
        pa.ensure_schema(conn)
        existing = pa.get_artifact(conn, art_id)
        if not open_actionable:
            if existing and existing.get("status") not in ("accepted", "rejected", "archived"):
                pa.update_artifact_state(conn, artifact_id=art_id, status="accepted",
                                         delivery_state="reviewed")
                print("[reminders] backlog empty -> stopped reminder row")
            conn.commit()
            return
        active = bool(existing and existing.get("status") in ("produced", "surfaced", "candidate"))
        if active:
            # get_artifact hydrates metadata under "metadata" (dict) and pops
            # "metadata_json"; pass it back verbatim so the reminder cadence is
            # preserved (upsert overwrites metadata_json with what we pass).
            md = existing.get("metadata")
            if not isinstance(md, dict):
                md = {}
            pa.upsert_artifact(conn, artifact_id=art_id, artifact_type="cron_run_output",
                               source_kind="backlog-triage", title=headline, summary=summary,
                               status=existing.get("status", "surfaced"),
                               delivery_state=existing.get("delivery_state", "emailed"),
                               priority=2, metadata=md)
            print("[reminders] refreshed active reminder row (cadence preserved)")
        else:
            md = {"reminder": _seed_reminder_state(time.time()), "source": "backlog-triage",
                  "open_issue_numbers": [i.get("number") for i in open_actionable]}
            pa.upsert_artifact(conn, artifact_id=art_id, artifact_type="cron_run_output",
                               source_kind="backlog-triage", title=headline, summary=summary,
                               status="produced", delivery_state="not_surfaced",
                               priority=2, metadata=md)
            try:
                pa.record_email_delivery(conn, artifact_id=art_id, message_id=message_id,
                                         subject="[UA backlog triage] " + headline,
                                         recipient=recipient)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] record_email_delivery: {exc}", file=sys.stderr)
            print("[reminders] seeded new reminder burst (+4h/+72h via the sweep)")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] reminder seed failed: {exc}", file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually email the digest from Simone")
    ap.add_argument("--dry-run", action="store_true", help="compose + print, do not email")
    ap.add_argument("--model", default="", help="override the LLM model id")
    ap.add_argument("--repo", default="", help="override the GitHub repo (owner/name)")
    ap.add_argument("--to", default="", help="override recipient email")
    ap.add_argument("--content-json", default="", help="path to a pre-synthesized triage JSON "
                    "(headline/assessment/actions/next_run_brief); skips the LLM call")
    ap.add_argument("--seed-reminders", action="store_true", help="also seed a reminder row so "
                    "the cron_artifact_reminders sweep fires +4h/+72h nudges (hybrid mode)")
    args = ap.parse_args()
    if args.repo:
        global REPO
        REPO = args.repo

    data = _gather()
    if args.content_json:
        try:
            triage = json.loads(Path(args.content_json).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] could not read --content-json: {exc}", file=sys.stderr)
            triage = _deterministic(data)
    else:
        triage = _synthesize(data, args.model) or _deterministic(data)
    body = _markdown(triage, data)
    subject = "[UA backlog triage] " + triage.get("headline", "assessment & guidance")[:140]

    if args.send and not args.dry_run:
        res = _send_email(subject, body, args.to)
        if args.seed_reminders:
            _seed_reminders(data, triage, _recipient(args.to), res.get("message_id", ""))
        return 0
    # dry-run / default: print only
    print(f"SUBJECT: {subject}\n\n{body}")
    if args.seed_reminders:
        print("[dry-run] would seed/refresh the reminder row (skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
