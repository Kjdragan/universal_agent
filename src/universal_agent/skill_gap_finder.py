"""
Weekly LLM "skill-gap finder" — mine recent Claude Code transcripts for recurring
manual workflows, repeated errors, and command lineages that *should* become skills.

This is the cron-shaped, Python re-implementation of the `technical-skill-finder`
SKILL recipes (assistant `tool_use` Bash commands, user `tool_result` with
`is_error == true`, real human prompts). It mines a rolling window of session
transcripts, dedupes candidate ideas against the existing `.claude/skills/*`
library, asks an LLM to synthesize + rank the gaps, and emits a human-gated
candidate report. With `--open-issue` it files ONE deduped GitHub issue labeled
`skill-gap` (mirrors the ci-failure-issue.yml dedup idiom: search by a stable
title prefix, skip if an open one already exists).

Inference routes through the **ZAI proxy / GLM models** — the same
Anthropic-emulation layer every UA autonomous loop uses
(`utils/model_resolution.resolve_sonnet` -> glm-5-turbo, client pointed at
`ANTHROPIC_BASE_URL`). No separate Anthropic spend, no new secret: ZAI creds
load from Infisical via `initialize_runtime_secrets()` exactly like the services.

Transcripts are read from `UA_CLAUDE_PROJECTS_DIR` (default `~/.claude/projects`).

Usage:
    python -m universal_agent.skill_gap_finder --dry-run --window-days 7   # count/size only, no creds
    python -m universal_agent.skill_gap_finder --window-days 7              # report to stdout/summary
    python -m universal_agent.skill_gap_finder --window-days 7 --open-issue # + one deduped GH issue
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

# src-layout: the `universal_agent` package lives under src/ and the project
# declares no build-system, so `uv sync` never installs it as an importable
# package. Put src/ on the path so the Infisical/ZAI bootstrap import in
# _load_zai_env() resolves on cron runners — not just shells that happen to
# export PYTHONPATH=src. Without this the weekly cron silently no-ops with
# "No module named 'universal_agent'" (false green).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

# A stable title prefix for GH-issue dedup (mirrors the ci-failure-issue.yml
# "search by stable title prefix" idiom).
ISSUE_TITLE_PREFIX = "[skill-gap] weekly skill-gap finder"
ISSUE_LABEL = "skill-gap"

MAX_CORPUS_CHARS = 60_000   # cap the corpus fed to the LLM
MAX_PROMPT_CHARS = 280      # cap any single mined human prompt
MAX_ERR_CHARS = 200         # cap any single mined error signature
TOP_CANDIDATES = 8          # candidates requested from the synthesis step

# Synthetic (non-human) user-line wrappers to ignore when mining real prompts.
_SYNTHETIC_PREFIXES = ("<task-notification>", "<task-id>", "<command-name>",
                       "<local-command", "<system-reminder>", "Caveat:")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")
_TOOL_ERR_WRAP_RE = re.compile(r"</?tool_use_error>")


# ──────────────────────────────────────────────────────────────────────────
# Secret/PII redaction.
#
# Transcripts are private and frequently contain credentials, tokens, and PII.
# This corpus is (a) sent to an EXTERNAL LLM proxy (ZAI/GLM) in _synthesize(),
# (b) echoed back into the GH issue body / stdout / systemd journal via the
# model's "evidence" field. We therefore SCRUB at every point raw transcript
# text is mined (_clean_err for errors, _mine for human prompts) so secrets
# never enter the corpus, AND defensively re-redact the final report string
# before it is printed or sent to `gh issue create --body`.
#
# Order matters: run the most specific/structured patterns first (provider
# keys, AWS, JWTs, emails, SSNs), then collapse any remaining long
# high-entropy tokens. Each pattern replaces with a typed [REDACTED-*] tag.
# ──────────────────────────────────────────────────────────────────────────
_REDACTION_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Anthropic keys: sk-ant-api03-..., sk-ant-...
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[REDACTED-ANTHROPIC-KEY]"),
    # OpenAI-style keys: sk-... / sk-proj-...
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}"), "[REDACTED-API-KEY]"),
    # GitHub tokens: ghp_, gho_, ghu_, ghs_, ghr_, github_pat_
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
     "[REDACTED-GH-TOKEN]"),
    # Slack tokens: xox[abprs]-...
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED-SLACK-TOKEN]"),
    # Google API keys: AIza...
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[REDACTED-GOOGLE-KEY]"),
    # AWS access key IDs: AKIA/ASIA + 16 uppercase alnum.
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED-AWS-KEY-ID]"),
    # AWS secret access keys: aws_secret... = <40 base64ish> OR a bare 40-char secret.
    # Use horizontal whitespace ([^\S\r\n]) so a match can never span a newline
    # and swallow the next corpus line.
    (re.compile(r"(?i)(aws_secret_access_key|aws_secret|secret_access_key)[^\S\r\n]*[=:][^\S\r\n]*\S+"),
     r"\1=[REDACTED-AWS-SECRET]"),
    (re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"),
     "[REDACTED-SECRET]"),
    # JWTs: header.payload.signature (three base64url segments).
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
     "[REDACTED-JWT]"),
    # Bearer / Authorization / credential assignments. Broad keyword set (incl.
    # common abbreviations like pw/pass/cred) so a value with no recognized key
    # prefix is the only thing the high-entropy fallback has to catch.
    (re.compile(r"(?i)\b(bearer|authorization|auth[_\-]?token|access[_\-]?token|api[_\-]?key|apikey|token|password|passwd|passphrase|pwd|pw|pass|credential|cred|secret|client[_\-]?secret|private[_\-]?key)\b[^\S\r\n]*[=:][^\S\r\n]*\S+"),
     r"\1=[REDACTED]"),
    # SSN-like: 123-45-6789.
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    # Email addresses.
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
     "[REDACTED-EMAIL]"),
)

# Collapse any remaining long, high-entropy alnum token (mixed case + digits)
# that survived the structured patterns — catches misc bearer/session blobs.
_HIGH_ENTROPY_RE = re.compile(
    r"(?<![A-Za-z0-9_/\-+=])"          # left boundary
    r"(?=[A-Za-z0-9_\-]{24,}\b)"        # at least 24 token chars ahead
    r"(?=[A-Za-z0-9_\-]*[A-Z])"         # has an uppercase
    r"(?=[A-Za-z0-9_\-]*[a-z])"         # has a lowercase
    r"(?=[A-Za-z0-9_\-]*\d)"            # has a digit
    r"[A-Za-z0-9_\-]{24,}\b"
)


def _redact(text: str) -> str:
    """Strip secrets/PII from a string before it leaves the machine or is logged.

    Applies typed regex substitutions for provider API keys, AWS keys/secrets,
    GitHub/Slack/Google tokens, JWTs, bearer/password/secret assignments, SSNs,
    and emails, then collapses any residual high-entropy token. Idempotent and
    safe to call on already-redacted text. Returns "" for falsy input.
    """
    if not text:
        return ""
    for pat, repl in _REDACTION_PATTERNS:
        text = pat.sub(repl, text)
    text = _HIGH_ENTROPY_RE.sub("[REDACTED-TOKEN]", text)
    return text


# ──────────────────────────────────────────────────────────────────────────
# Pure, importable, testable helpers (stdlib-only).
# ──────────────────────────────────────────────────────────────────────────
def _projects_dir() -> Path:
    """Resolve the Claude Code projects dir from env or the default home path."""
    env = (os.getenv("UA_CLAUDE_PROJECTS_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude" / "projects"


def _recent_transcripts(projects_dir: Path, window_days: int) -> list[Path]:
    """Return *.jsonl transcripts modified within the last ``window_days``.

    Globs ``**/*.jsonl`` under ``projects_dir`` and filters by mtime. Sorted
    newest-first so the corpus builder favors the most recent activity.
    """
    projects_dir = Path(projects_dir)
    if not projects_dir.is_dir():
        return []
    cutoff = time.time() - max(0, window_days) * 86_400
    out: list[Path] = []
    for p in projects_dir.glob("**/*.jsonl"):
        try:
            if p.is_file() and p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            continue
    out.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return out


def _read_jsonl(path: Path) -> list[dict]:
    """Tolerantly read a JSONL transcript: skip binary/corrupt lines."""
    records: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        return []
    return records


def _clean_err(text: str) -> str:
    """Strip ANSI + tool_use_error wrappers, REDACT secrets, collapse, cap length."""
    text = _ANSI_RE.sub("", text or "")
    text = _TOOL_ERR_WRAP_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Redact BEFORE capping so a secret never survives by sitting past MAX_ERR_CHARS.
    text = _redact(text)
    return text[:MAX_ERR_CHARS]


def _content_text(content) -> str:
    """Best-effort extract text from a tool_result content (str or list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                parts.append(str(blk.get("text") or blk.get("content") or ""))
            else:
                parts.append(str(blk))
        return "\n".join(parts)
    return str(content or "")


def _mine(records: list[dict]) -> dict:
    """Collect repeated Bash commands + error signatures + prompt themes.

    Re-implements the technical-skill-finder recipes in Python:
      - Recipe A: assistant tool_use Bash commands
      - Recipe B: user tool_result with is_error == true (ANSI-stripped)
      - Recipe D: real human prompts (plain-string user content, no synthetic wrappers)

    Returns a dict of Counters: {"bash": Counter, "errors": Counter, "prompts": Counter}.
    """
    bash: Counter = Counter()
    errors: Counter = Counter()
    prompts: Counter = Counter()

    for rec in records:
        rtype = rec.get("type")
        msg = rec.get("message") or {}
        if rtype == "assistant":
            content = msg.get("content") or []
            if isinstance(content, list):
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") == "tool_use" and blk.get("name") == "Bash":
                        cmd = ((blk.get("input") or {}).get("command") or "").strip()
                        if cmd:
                            # Key on the leading token(s) so reruns with varying
                            # args still cluster (the recurring-workflow signal).
                            head = " ".join(cmd.split()[:3])
                            bash[head] += 1
        elif rtype == "user":
            content = msg.get("content")
            # Recipe D: real human prompt = plain-string content, not synthetic.
            if isinstance(content, str):
                stripped = content.strip()
                if stripped and not stripped.startswith(_SYNTHETIC_PREFIXES):
                    # Redact human prompts BEFORE capping/counting: they routinely
                    # contain pasted credentials, tokens, and PII that must never
                    # enter the corpus sent to the external LLM proxy.
                    theme = _redact(stripped)[:MAX_PROMPT_CHARS]
                    if theme:
                        prompts[theme] += 1
            # Recipe B: error tool_results.
            elif isinstance(content, list):
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") == "tool_result" and blk.get("is_error") is True:
                        err = _clean_err(_content_text(blk.get("content")))
                        if err:
                            errors[err] += 1

    return {"bash": bash, "errors": errors, "prompts": prompts}


def _existing_skill_names(skills_dir: Path) -> list[str]:
    """List existing skill directory names (those that ship a SKILL.md).

    Used to dedupe candidate ideas against the existing skill library.
    """
    skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        return []
    names = []
    for d in sorted(skills_dir.iterdir()):
        try:
            if d.is_dir() and (d / "SKILL.md").is_file():
                names.append(d.name)
        except OSError:
            continue
    return names


def _build_corpus(mined: dict, existing_skills: list[str], window_days: int,
                  transcript_count: int) -> str:
    """Render the mined signals + existing-skill list into an LLM corpus string."""
    bash: Counter = mined.get("bash", Counter())
    errors: Counter = mined.get("errors", Counter())
    prompts: Counter = mined.get("prompts", Counter())

    lines: list[str] = []
    lines.append(f"# Skill-gap corpus (last {window_days}d, {transcript_count} transcripts)")
    lines.append("")
    lines.append("## Recurring Bash command heads (count >= 2)")
    for cmd, n in bash.most_common(40):
        if n >= 2:
            # Bash command heads can carry inline secrets (e.g. an exported token
            # or `-H 'Authorization: Bearer ...'`) — redact at render time too.
            lines.append(f"- ({n}x) {_redact(cmd)}")
    lines.append("")
    lines.append("## Recurring error signatures (count >= 2)")
    for err, n in errors.most_common(40):
        if n >= 2:
            lines.append(f"- ({n}x) {_redact(err)}")
    lines.append("")
    lines.append("## Repeated human prompt themes")
    for prm, n in prompts.most_common(30):
        lines.append(f"- ({n}x) {_redact(prm)}")
    lines.append("")
    lines.append("## Existing skills (DEDUP against these — do not re-propose)")
    lines.append(", ".join(existing_skills) if existing_skills else "(none found)")
    # Final belt-and-suspenders pass over the whole corpus before it leaves the box.
    return _redact("\n".join(lines))[:MAX_CORPUS_CHARS]


def _build_report(candidates: list[dict]) -> str:
    """Render ranked candidate dicts into a human-gated markdown report."""
    lines: list[str] = ["# Weekly skill-gap finder — ranked candidates", ""]
    if not candidates:
        lines.append("No new skill-gap candidates surfaced this window.")
        return "\n".join(lines)
    lines.append(f"{len(candidates)} candidate skill(s) surfaced (human review required "
                 "before any are built):")
    lines.append("")
    for i, c in enumerate(candidates, 1):
        title = c.get("title") or c.get("name") or f"candidate {i}"
        score = c.get("score", c.get("confidence", "?"))
        lines.append(f"## {i}. {title}  (score: {score})")
        if c.get("problem"):
            lines.append(f"- **Problem:** {c['problem']}")
        if c.get("evidence"):
            ev = c["evidence"]
            ev = ev if isinstance(ev, str) else "; ".join(str(x) for x in ev)
            lines.append(f"- **Evidence:** {ev}")
        if c.get("frequency") is not None:
            lines.append(f"- **Frequency:** {c['frequency']}")
        kind = c.get("skill_fit") or c.get("kind")
        if kind:
            lines.append(f"- **Fit:** {kind}")
        lines.append("")
    lines.append("---")
    lines.append("_Candidates are advisory. A human must approve before any skill is created._")
    # Defensive re-redaction: the SYSTEM prompt asks the model to populate an
    # 'evidence' field from the corpus, so a candidate's title/problem/evidence
    # can echo secrets back. Scrub the rendered report BEFORE it is printed to
    # stdout (systemd journal) or sent to `gh issue create --body`.
    return _redact("\n".join(lines))


# ──────────────────────────────────────────────────────────────────────────
# LLM synthesis + ranking (heavy imports lazy, inside functions).
# ──────────────────────────────────────────────────────────────────────────
def _load_zai_env() -> None:
    """Best-effort: load ZAI (ANTHROPIC_*->GLM) env via Infisical, like the UA services."""
    if os.getenv("ANTHROPIC_BASE_URL") and (os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")):
        return  # already configured (e.g. running on the VPS)
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
        initialize_runtime_secrets()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Infisical/ZAI bootstrap skipped: {exc}", file=sys.stderr)


def _client():
    from anthropic import Anthropic  # local import; pkg may be absent in minimal envs
    api_key = (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
               or os.getenv("ZAI_API_KEY"))
    if not api_key:
        return None
    kwargs = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return Anthropic(**kwargs)


SYSTEM = (
    "You are a skill-gap analyst for the Universal Agent codebase. You are given a corpus "
    "mined from recent Claude Code session transcripts: recurring Bash command sequences, "
    "recurring error signatures, repeated human prompt themes, and the list of EXISTING "
    "skills. Identify NEW skills worth building — recurring manual workflows, repeated "
    "errors, or multi-step command lineages — that are NOT already covered by an existing "
    "skill. Dedupe hard against the existing-skills list. Prioritize by frequency, impact, "
    "and actionability. Respond with ONLY a JSON object: "
    '{"candidates":[{"title":"...","problem":"...","evidence":"...","frequency":N,'
    '"skill_fit":"new|update <existing-skill>","score":0.0}]}. '
    "Empty candidates list if nothing recurs enough to justify a skill."
)


def _synthesize(client, model: str, corpus: str, top_n: int) -> list[dict]:
    """Ask the LLM to synthesize + rank skill-gap candidates from the corpus."""
    user = (f"Return at most {top_n} ranked candidates.\n\n{corpus}")
    resp = client.messages.create(
        model=model, max_tokens=2000, system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
    # tolerate code-fenced JSON (leading ``` or ```json)
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip() if "```" in raw[3:] else raw.strip("`")
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    if not isinstance(candidates, list):
        return []
    # Sort by score descending (string scores fall back to 0.0).
    def _score(c):
        try:
            return float(c.get("score", 0.0))
        except (TypeError, ValueError):
            return 0.0
    candidates.sort(key=_score, reverse=True)
    return candidates[:top_n]


def _open_issue(report: str, n_candidates: int) -> None:
    """Open ONE deduped GH issue labeled skill-gap (ci-failure-issue.yml idiom).

    Dedup: search open issues by the stable title prefix; skip create if one
    already exists. Failure only warns — a gh outage must never break the cron.
    """
    # Defense-in-depth: re-redact even though _build_report() already scrubs, so
    # the issue body is safe regardless of how `report` was produced.
    report = _redact(report)
    try:
        existing = subprocess.run(
            ["gh", "issue", "list", "--label", ISSUE_LABEL, "--state", "open",
             "--search", f"in:title \"{ISSUE_TITLE_PREFIX}\"", "--json", "number",
             "--jq", "length"],
            check=True, cwd=REPO_ROOT, capture_output=True, text=True,
        )
        count = int((existing.stdout or "0").strip() or "0")
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Could not query existing skill-gap issues: {exc}")
        count = 0

    if count > 0:
        print(f"Open skill-gap issue already exists ({count}); skipping create.")
        return

    title = f"{ISSUE_TITLE_PREFIX}: {n_candidates} candidate(s)"
    try:
        subprocess.run(
            ["gh", "issue", "create", "--title", title, "--label", ISSUE_LABEL,
             "--body", report],
            check=True, cwd=REPO_ROOT,
        )
        print(f"\nOpened GH issue: {title}")
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Could not open GH issue: {exc}")


# ──────────────────────────────────────────────────────────────────────────
# CLI entrypoint.
# ──────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Weekly ZAI-backed skill-gap finder")
    ap.add_argument("--window-days", type=int, default=7,
                    help="transcript mtime window to mine (default 7)")
    ap.add_argument("--model", default="",
                    help="override model (default resolve_sonnet -> glm-5-turbo)")
    ap.add_argument("--open-issue", action="store_true",
                    help="open ONE deduped GH issue labeled skill-gap")
    ap.add_argument("--email", action="store_true",
                    help="also send a one-shot Simone email of this week's candidates")
    ap.add_argument("--dry-run", action="store_true",
                    help="print transcript count + corpus size only; no LLM/secrets")
    args = ap.parse_args(argv)

    projects_dir = _projects_dir()
    transcripts = _recent_transcripts(projects_dir, args.window_days)
    skills_dir = REPO_ROOT / ".claude" / "skills"

    # Mine the corpus (stdlib-only). Keep --dry-run fast: mine but never import
    # anthropic/infisical/model_resolution or touch secrets.
    records: list[dict] = []
    for p in transcripts:
        records.extend(_read_jsonl(p))
    mined = _mine(records)
    existing_skills = _existing_skill_names(skills_dir)
    corpus = _build_corpus(mined, existing_skills, args.window_days, len(transcripts))

    if args.dry_run:
        print(f"transcripts={len(transcripts)} corpus_chars={len(corpus)} "
              f"records={len(records)} existing_skills={len(existing_skills)}")
        return 0

    _load_zai_env()
    client = _client()
    if client is None:
        print("::warning::No ZAI/Anthropic creds available — skipping skill-gap finder "
              "(set INFISICAL_* or ANTHROPIC_* env).")
        return 0

    try:
        from universal_agent.utils.model_resolution import resolve_sonnet
        model = args.model or resolve_sonnet()
    except Exception:  # noqa: BLE001
        model = args.model or "glm-5-turbo"

    print(f"Synthesizing skill-gap candidates via model={model} "
          f"({len(transcripts)} transcripts, {len(corpus)} corpus chars)\n")
    try:
        candidates = _synthesize(client, model, corpus, TOP_CANDIDATES)
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Skill-gap synthesis failed: {type(exc).__name__}: {exc}")
        return 0

    # _build_report() already redacts; re-redact defensively at the boundaries
    # where the report leaves the process (stdout/journal, step-summary file,
    # GH issue body) so a future refactor can't reintroduce a leak.
    report = _redact(_build_report(candidates))
    print("\n" + report)

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            Path(summary).write_text(report, encoding="utf-8")
        except OSError as exc:
            print(f"[warn] could not write GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)

    if candidates and args.open_issue:
        _open_issue(report, len(candidates))

    if candidates and args.email:
        try:
            from universal_agent.simone_mail import send_simone_email

            subject = f"[UA skill-gap] {len(candidates)} candidate(s) surfaced this week"
            res = send_simone_email(subject=subject, text=report, source="skill-gap-finder")
            print(f"[email] {res.get('status')} "
                  f"{res.get('message_id') or res.get('reason', '')}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] one-shot email failed: {exc}", file=sys.stderr)

    return 0  # informational — candidates are advisory, never a build failure


if __name__ == "__main__":
    sys.exit(main())
