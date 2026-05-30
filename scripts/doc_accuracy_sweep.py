"""
Nightly LLM documentation-accuracy auditor (the "judging" half of the doc engine).

The deterministic gate (doc_audit.py) proves a doc is internally consistent (links,
symbol refs, frontmatter, no line numbers). THIS script proves a doc is still TRUE: it
takes the N oldest-verified docs, reads each doc + the code its `code_paths` frontmatter
claims to document, and asks an LLM to compare them and report drift.

Inference routes through the **ZAI proxy / GLM models** — the same Anthropic-emulation
layer every UA autonomous loop uses (`utils/model_resolution.resolve_sonnet` → glm-5-turbo,
client pointed at `ANTHROPIC_BASE_URL`). No separate Anthropic spend, no new secret: ZAI
creds load from Infisical via `initialize_runtime_secrets()` exactly like the services.

Selection reuses `doc_audit.build_accuracy_batch` (oldest `last_verified` first), so the
whole corpus is re-verified on a rotating cadence.

Usage:
    python scripts/doc_accuracy_sweep.py --batch 8                 # report to stdout/summary
    python scripts/doc_accuracy_sweep.py --batch 8 --open-issue     # + open a GH issue on drift
    python scripts/doc_accuracy_sweep.py --batch 1 --dry-run        # build prompts, no LLM call
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

# Reuse the deterministic engine's batch builder (same scripts/ dir).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doc_audit import DOCS_DIR, REPO_ROOT, build_accuracy_batch  # noqa: E402

# src-layout: the `universal_agent` package lives under src/ and the project declares no
# build-system, so `uv sync` never installs it as an importable package. Put src/ on the
# path so the Infisical/ZAI bootstrap import in _load_zai_env() resolves on CI runners —
# not just local shells that happen to export PYTHONPATH=src. Without this the nightly
# sweep silently no-ops with "[warn] ... No module named 'universal_agent'" (false green).
sys.path.insert(0, str(REPO_ROOT / "src"))

MAX_CODE_CHARS = 48_000   # cap code context fed to the judge (per doc)
MAX_FILE_CHARS = 16_000   # cap any single source file


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


def _read_code_for(code_paths: list[str]) -> str:
    """Concatenate (capped) the source the doc claims to document."""
    chunks, total = [], 0
    for glob in code_paths:
        glob = (glob or "").strip().strip('"').strip("'")
        if not glob or "::" in glob:
            continue
        matches = sorted(REPO_ROOT.glob(glob)) if any(c in glob for c in "*?[") else [REPO_ROOT / glob]
        for m in matches:
            if not m.is_file() or total >= MAX_CODE_CHARS:
                continue
            try:
                body = m.read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_CHARS]
            except Exception:  # noqa: BLE001
                continue
            rel = m.relative_to(REPO_ROOT)
            chunk = f"\n===== {rel} =====\n{body}\n"
            chunks.append(chunk)
            total += len(chunk)
    return "".join(chunks)[:MAX_CODE_CHARS]


SYSTEM = (
    "You are a documentation-accuracy auditor for the Universal Agent codebase. You are given "
    "ONE documentation file and excerpts of the source code it claims to document. Decide whether "
    "the doc still accurately describes the code. Judge load-bearing claims only (behavior, flow, "
    "function/class roles, env-var/flag names, defaults, file paths) — ignore prose style. "
    "Code is the source of truth. Respond with ONLY a JSON object: "
    '{"verdict":"accurate|minor_drift|major_drift","findings":[{"severity":"P1|P2",'
    '"doc_claim":"...","code_reality":"..."}]}. Empty findings if accurate.'
)


def _judge(client, model: str, doc_rel: str, doc_text: str, code_text: str) -> dict:
    user = (f"# DOC: {doc_rel}\n{doc_text}\n\n# CODE IT DOCUMENTS\n"
            f"{code_text or '(no resolvable code_paths)'}")
    resp = client.messages.create(
        model=model, max_tokens=1500, system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
    # tolerate code-fenced JSON
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip() if "```" in raw[3:] else raw.strip("`")
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"verdict": "parse_error", "findings": [], "_raw": raw[:400]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Nightly ZAI-backed doc-accuracy sweep")
    ap.add_argument("--batch", type=int, default=8, help="oldest-verified docs to audit")
    ap.add_argument("--model", default="", help="override model (default resolve_sonnet → glm-5-turbo)")
    ap.add_argument("--open-issue", action="store_true", help="open a GH issue if drift is found")
    ap.add_argument("--dry-run", action="store_true", help="build prompts only; no LLM call")
    args = ap.parse_args()

    batch = build_accuracy_batch(args.batch)
    if not batch:
        print("No docs to audit.")
        return 0

    if args.dry_run:
        for d in batch:
            code = _read_code_for(d.get("code_paths", []))
            print(f"{d['doc']}: code_paths={d.get('code_paths')} code_chars={len(code)}")
        return 0

    _load_zai_env()
    client = _client()
    if client is None:
        print("::warning::No ZAI/Anthropic creds available — skipping accuracy sweep "
              "(set INFISICAL_* or ANTHROPIC_* env).")
        return 0

    try:
        from universal_agent.utils.model_resolution import resolve_sonnet
        model = args.model or resolve_sonnet()
    except Exception:  # noqa: BLE001
        model = args.model or "glm-5-turbo"

    print(f"Auditing {len(batch)} docs via model={model}\n")
    drifted = []
    for d in batch:
        doc_rel = d["doc"]
        doc_text = (DOCS_DIR / doc_rel).read_text(encoding="utf-8", errors="ignore")
        code_text = _read_code_for(d.get("code_paths", []))
        try:
            verdict = _judge(client, model, doc_rel, doc_text, code_text)
        except Exception as exc:  # noqa: BLE001
            print(f"  [error] {doc_rel}: {type(exc).__name__}: {exc}")
            continue
        v = verdict.get("verdict", "?")
        n = len(verdict.get("findings", []))
        print(f"  {v:12} {doc_rel}  ({n} finding(s))")
        if v in ("minor_drift", "major_drift") and verdict.get("findings"):
            drifted.append((doc_rel, verdict))

    # ---- report ----
    lines = [f"# Nightly doc-accuracy sweep — {len(batch)} docs audited (model={model})", ""]
    if not drifted:
        lines.append("✅ No drift detected in this batch.")
    else:
        lines.append(f"⚠️ Drift detected in {len(drifted)} doc(s):\n")
        for doc_rel, verdict in drifted:
            lines.append(f"## `{doc_rel}` — {verdict['verdict']}")
            for f in verdict.get("findings", []):
                lines.append(f"- **[{f.get('severity','?')}]** doc: {f.get('doc_claim','')}")
                lines.append(f"  - code: {f.get('code_reality','')}")
            lines.append("")
    report = "\n".join(lines)
    print("\n" + report)

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(report, encoding="utf-8")

    if drifted and args.open_issue:
        title = f"doc-accuracy: drift in {len(drifted)} doc(s) [nightly]"
        try:
            subprocess.run(["gh", "issue", "create", "--title", title,
                            "--label", "documentation", "--body", report],
                           check=True, cwd=REPO_ROOT)
            print(f"\nOpened GH issue: {title}")
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::Could not open GH issue: {exc}")

    return 0  # informational — drift is reported, not a build failure


if __name__ == "__main__":
    sys.exit(main())
