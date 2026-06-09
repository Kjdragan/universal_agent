"""
Advisory deslop reviewer for PR diffs (report-only — never blocks a merge).

Takes a *pre-computed* diff file (the merge-base delta of a PR), feeds it to an
LLM running the `technical-deslop` rubric in REPORT-ONLY mode, and prints a
friendly markdown comment to stdout for a downstream `gh pr comment` step. The
LLM flags AI-generated slop (redundant comments, over-broad excepts/swallows,
dead defensive checks, narration logging, needless casts/type-ignores,
copy-paste dup) while KEEPING error contracts, observability, security checks,
and public docstrings. It only reports — it never edits code or changes behavior.

Inference routes through the **ZAI proxy / GLM models** — the same Anthropic-
emulation layer every UA autonomous loop uses (`resolve_sonnet` → glm-5-turbo,
client pointed at `ANTHROPIC_BASE_URL`). No separate Anthropic spend, no new
secret: ZAI creds load from Infisical via `initialize_runtime_secrets()`.

This tool ALWAYS exits 0. On no creds, empty diff, LLM error, or parse failure
it prints a short note and a "no slop found" comment so the workflow stays green
and can never block a PR or auto-merge.

Usage:
    python scripts/deslop_advisory.py --diff /tmp/pr.diff
    python scripts/deslop_advisory.py --diff /tmp/pr.diff --model glm-5-turbo
    python scripts/deslop_advisory.py --diff /tmp/pr.diff --max-bytes 60000
    python scripts/deslop_advisory.py --diff /tmp/pr.diff --meta-out /tmp/meta.json

The optional ``--meta-out`` flag writes a small JSON sidecar describing the
findings — ``{"count": N, "max_severity": "none|low|medium|high",
"severities": [...]}`` — so a downstream workflow can decide whether the
findings warrant a durable tracking issue (medium/high) or just a PR comment
(low/none) WITHOUT re-parsing the markdown. Writing the sidecar never affects
the exit code: this tool ALWAYS exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

# src-layout: the `universal_agent` package lives under src/ and the project
# declares no build-system, so `uv sync` never installs it as an importable
# package. Put src/ on the path so the Infisical/ZAI bootstrap import in
# _load_zai_env() resolves on CI runners — not just local shells that happen to
# export PYTHONPATH=src.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_MAX_BYTES = 60_000  # cap diff fed to the judge (huge diffs are truncated)


def _read_diff(path: str) -> str:
    """Read a pre-computed diff file; empty string if missing/unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _truncate(text: str, max_bytes: int) -> str:
    """Truncate text to at most ``max_bytes`` UTF-8 bytes, appending a marker."""
    if max_bytes <= 0:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return clipped + "\n\n...[diff truncated for length]..."


def _parse_llm_json(raw: str) -> dict:
    """Parse the LLM response into a dict; tolerant of code fences, {} on failure."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    # tolerate code-fenced JSON
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip() if "```" in raw[3:] else raw.strip("`")
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}


_SEVERITY_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}

# Stable hidden marker so the workflow can find-and-upsert ONE comment per PR
# (re-runs edit the existing comment instead of stacking new ones). Emitted as
# the first line of the comment markdown; GitHub renders HTML comments invisibly.
COMMENT_MARKER = "<!-- deslop-advisory -->"

# Severity ranking for computing the meta sidecar's max_severity.
_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


def _collect_severities(suggestions: list) -> list:
    """Pull normalized (lowercased) severities for the dict suggestions only."""
    out = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        out.append(str(s.get("severity", "")).lower())
    return out


def _max_severity(severities: list) -> str:
    """Highest severity present ('none' if empty / no recognized severities)."""
    best = "none"
    best_rank = 0
    for sev in severities:
        rank = _SEVERITY_RANK.get(sev, 0)
        if rank > best_rank:
            best_rank = rank
            best = sev
    return best


def _build_meta(suggestions: list) -> dict:
    """Build the JSON sidecar: count, max_severity, and the per-finding severities."""
    severities = _collect_severities(suggestions)
    return {
        "count": len(severities),
        "max_severity": _max_severity(severities),
        "severities": severities,
    }


def _write_meta(path: str, suggestions: list) -> None:
    """Best-effort: write the meta sidecar; never raise (must not affect exit 0)."""
    try:
        Path(path).write_text(
            json.dumps(_build_meta(suggestions)), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[warn] could not write meta sidecar {path}: {exc}", file=sys.stderr)


def _build_comment(suggestions: list) -> str:
    """Render the advisory suggestions as a friendly report-only markdown comment."""
    header = f"{COMMENT_MARKER}\n## 🧹 Deslop advisory (report-only)"
    note = (
        "_Advisory only — this never blocks a PR or auto-merge. "
        "Behavior-preserving suggestions from the `technical-deslop` rubric._"
    )
    if not suggestions:
        return f"{header}\n\n{note}\n\n✅ No slop found in this diff."

    lines = [header, "", note, "", f"Found {len(suggestions)} suggestion(s):", ""]
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        sev = str(s.get("severity", "?")).lower()
        icon = _SEVERITY_ICON.get(sev, "•")
        file = s.get("file", "(unknown)")
        issue = s.get("issue", "")
        fix = s.get("fix", "")
        lines.append(f"- {icon} **[{sev}]** `{file}` — {issue}")
        if fix:
            lines.append(f"  - _fix_: {fix}")
    lines.append("")
    return "\n".join(lines)


def _load_zai_env() -> None:
    """Best-effort: load ZAI (ANTHROPIC_*→GLM) env via Infisical, like the UA services."""
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


# --------------------------------------------------------------------------- #
# KEEP list — single source of truth is
# .claude/skills/technical-deslop/references/slop-patterns.md (the block between
# the KEEP-LIST markers). We load it verbatim so the CI finder, the skill, and
# the auto-remediation brief can never drift. _KEEP_FALLBACK is an in-tree copy
# used ONLY if that file is missing/unreadable (e.g. a stripped checkout); a unit
# test asserts the two stay byte-identical.
# --------------------------------------------------------------------------- #
_SLOP_PATTERNS_DOC = (
    REPO_ROOT
    / ".claude"
    / "skills"
    / "technical-deslop"
    / "references"
    / "slop-patterns.md"
)
_KEEP_BEGIN = "<!-- KEEP-LIST:BEGIN -->"
_KEEP_END = "<!-- KEEP-LIST:END -->"

_KEEP_FALLBACK = """\
1. **Real error contracts** — `try/except` that maps/raises a domain error, retries, releases a resource, or returns a documented fallback; any handler that does real work or preserves an API guarantee.
2. **Structured logging / observability** — `logfire`/`langsmith` spans, `logger.*` with structured fields/context, trace instrumentation, metrics. Observability is load-bearing here (Logfire setup ordering even drives the `E402` ignore). Don't mistake telemetry for narration.
3. **Security / input validation** — `defusedxml`, auth/JWT checks, path/host allowlists, sanitization before shell/SQL/network calls, and the surgical `# noqa: F821` defensive `globals()` patterns the gate intentionally preserves.
4. **Public-API docstrings** — module/class/public-function docstrings (consumed by mkdocs-material), param semantics not obvious from types, units, side effects, raised exceptions.
5. **"Why / when / which-mode" comments** — any comment that explains WHY something is done, WHEN it applies, or under WHICH deployment/runtime mode (in-process vs standalone systemd timer, dev vs prod profile, an external-library quirk, the intent of a regex, a legal note). Rationale, never "redundant restating code", even when multi-line. When in doubt, KEEP.
6. **Dated decision / migration notes** — `# YYYY-MM-DD — …`, `# YYYY-MM-DD: …`, `# Removed YYYY-MM-DD: …`, `# Migrated YYYY-MM-DD …` and similar dated rationale (with or without a leading verb), plus load-bearing `# noqa` annotations documented in `pyproject.toml`/CI. Don't strip documented decisions.
7. **Type annotations themselves**, and `cast`/`# type: ignore` that are actually required for the checker to pass. Only the *redundant* ones are slop."""


def _load_keep_rules() -> str:
    """The authoritative KEEP list, loaded verbatim from slop-patterns.md so the
    finder never drifts from the skill. Falls back to the in-tree copy if the
    file is absent/unreadable — the advisory must never break on a stripped
    checkout."""
    try:
        text = _SLOP_PATTERNS_DOC.read_text(encoding="utf-8")
        if _KEEP_BEGIN in text and _KEEP_END in text:
            block = text.split(_KEEP_BEGIN, 1)[1].split(_KEEP_END, 1)[0].strip()
            if block:
                return block
    except OSError:
        pass
    return _KEEP_FALLBACK


SYSTEM = (
    "You are an advisory deslop reviewer for the Universal Agent codebase, applying the "
    "`technical-deslop` rubric in REPORT-ONLY mode. You are given a unified diff of a pull "
    "request (changed hunks only). Identify AI-generated noise (slop) the change introduced, "
    "behavior-preserving suggestions ONLY — you never edit code, never change logic, never "
    "rename, never restructure control flow.\n\n"
    "FLAG: redundant comments restating code; docstrings echoing the signature; over-broad "
    "try/except that swallow (except Exception: pass / return None) with no real failure mode; "
    "dead defensive None-checks that can't trigger; verbose narration logging "
    "(logger.info('Starting foo'), print('done')); needless casts / # type: ignore where the "
    "types already align; needless intermediate variables; dead generation scaffolding (unused "
    "imports, empty f-strings, commented-out old blocks); restating-the-obvious section "
    "banners; copy-paste duplication.\n\n"
    "KEEP — never flag (authoritative, mirrored verbatim from "
    "`.claude/skills/technical-deslop/references/slop-patterns.md`):\n"
    f"{_load_keep_rules()}\n\n"
    "TIE-BREAKER (decisive): a comment is 'redundant restating code' ONLY when it restates "
    "WHAT one adjacent statement literally does (e.g. `# increment i` over `i += 1`). A comment "
    "that explains WHY, WHEN, or under WHICH deployment/runtime mode something happens — or a "
    "dated decision/migration note (`# YYYY-MM-DD — …`) — is rationale, NEVER redundant, even "
    "when it spans multiple lines. When a comment could be read either way, KEEP wins: do NOT "
    "flag it.\n"
    "EVIDENCE REQUIRED: for every comment you flag, quote the exact comment text verbatim in "
    "`issue` and add one sentence justifying why it is slop and NOT rationale / a why-comment / "
    "a dated decision. If you cannot, do not flag it.\n\n"
    "When unsure whether a pattern is intentional, do NOT flag it. Respond with ONLY a JSON "
    'object: {"suggestions":[{"file":"path","severity":"high|medium|low","issue":"...",'
    '"fix":"..."}]}. Empty suggestions array if the diff has no slop.'
)


def _review(client, model: str, diff_text: str) -> dict:
    user = f"# PR DIFF (review for slop; report only)\n\n{diff_text}"
    resp = client.messages.create(
        model=model, max_tokens=2000, system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
    return _parse_llm_json(raw)


def _is_autoremediation_branch() -> bool:
    """True when running on a deslop auto-remediation fix branch — observe-mode
    ``deslop/observe-fix-*`` or auto-merge-mode ``claude/deslop-fix-*``. GitHub
    Actions sets ``GITHUB_HEAD_REF`` to the PR source branch on ``pull_request``
    events. Reviewing the deslopper's own cleanup PR for slop is noise — it
    reliably finds nothing and only pings the operator — so the advisory
    short-circuits on these branches.
    """
    head = os.getenv("GITHUB_HEAD_REF", "")
    return head.startswith("claude/deslop-fix") or head.startswith("deslop/")


def _notify_operator() -> bool:
    """Comment on every PR when truthy; default off limits comments to med/high."""
    return os.getenv("UA_DESLOP_NOTIFY_OPERATOR", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Advisory deslop reviewer (report-only)")
    ap.add_argument("--diff", required=True, help="path to a pre-computed diff file")
    ap.add_argument("--model", default="", help="override model (default resolve_sonnet → glm-5-turbo)")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help="truncate the diff to this many UTF-8 bytes before judging")
    ap.add_argument("--meta-out", default="",
                    help="optional path for a JSON findings sidecar "
                         "(count / max_severity / severities)")
    args = ap.parse_args()

    # Skip entirely on the auto-remediation fix branches the dispatcher creates:
    # emit nothing (empty stdout + no meta sidecar) so the workflow's comment and
    # issue steps both no-op. Keeps the deslopper's own cleanup PRs noise-free.
    if _is_autoremediation_branch():
        print(
            f"[note] advisory skipped on auto-remediation branch "
            f"{os.getenv('GITHUB_HEAD_REF', '')!r} — no comment/issue emitted.",
            file=sys.stderr,
        )
        return 0

    def _emit(suggestions: list) -> int:
        """Write the meta sidecar; comment only on actionable (med/high) findings."""
        if args.meta_out:
            _write_meta(args.meta_out, suggestions)
        # The workflow reuses this comment verbatim as the tracking-issue body, so
        # med/high must still print (they file an issue anyway); none/low stay
        # silent — no comment, no issue, no operator email.
        max_sev = _max_severity(_collect_severities(suggestions))
        if _notify_operator() or max_sev in {"medium", "high"}:
            print(_build_comment(suggestions))
        return 0  # always advisory — never blocks a PR or auto-merge

    diff_text = _truncate(_read_diff(args.diff), args.max_bytes)
    if not diff_text.strip():
        print("[note] Empty or unreadable diff — nothing to review.", file=sys.stderr)
        return _emit([])

    _load_zai_env()
    client = _client()
    if client is None:
        print("[note] No ZAI/Anthropic creds available — skipping deslop advisory "
              "(set INFISICAL_* or ANTHROPIC_* env).", file=sys.stderr)
        return _emit([])

    try:
        from universal_agent.utils.model_resolution import resolve_sonnet
        model = args.model or resolve_sonnet()
    except Exception:  # noqa: BLE001
        model = args.model or "glm-5-turbo"

    try:
        verdict = _review(client, model, diff_text)
    except Exception as exc:  # noqa: BLE001
        print(f"[note] LLM review failed ({type(exc).__name__}: {exc}) — emitting empty advisory.",
              file=sys.stderr)
        return _emit([])

    suggestions = verdict.get("suggestions", [])
    if not isinstance(suggestions, list):
        suggestions = []
    return _emit(suggestions)


if __name__ == "__main__":
    sys.exit(main())
