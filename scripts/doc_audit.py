"""
Documentation Audit Engine (project_docs/) — replaces the legacy doc_drift_auditor.py.

Two layers, per docs/00_DOCUMENTATION_REFACTOR_PLAN.md §3:

  DETERMINISTIC (this file, runs in CI on every PR — the enforcement teeth):
    1. frontmatter schema     — every doc has required keys; code_paths globs resolve
    2. symbol-reference check  — every `file::symbol` citation grep-resolves in that file
    3. no-line-number check    — citation-style line refs are forbidden (they rot)
    4. internal-link check     — relative markdown links resolve
    5. orphan/index check      — every canonical doc is linked from README.md (when present)

  LLM ACCURACY (rotating, nightly — `build_accuracy_batch`):
    selects the N docs with the oldest `last_verified`, hands each (doc + its code_paths)
    to an LLM judge that compares doc claims to code. Implemented as a batch-builder here;
    the LLM call itself is driven by the nightly workflow so this module stays dependency-free
    and CI-safe.

Usage:
    python scripts/doc_audit.py                  # audit project_docs/, exit 1 on errors
    python scripts/doc_audit.py --warn-only       # never exit nonzero (report only)
    python scripts/doc_audit.py --accuracy-batch 10   # print oldest-verified docs as JSON
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(
    subprocess.run(["git", "rev-parse", "--show-toplevel"],
                   capture_output=True, text=True, check=True).stdout.strip()
)
DOCS_DIR = REPO_ROOT / "project_docs"
README = DOCS_DIR / "README.md"

REQUIRED_FRONTMATTER = ["title", "status", "canonical", "subsystem", "code_paths", "last_verified"]

# Files that are meta (no code_paths expected to resolve, no symbol refs required)
META_DOCS = {"00_DOCUMENTATION_REFACTOR_PLAN.md", "01_TAXONOMY.md", "02_GOTCHA_INVENTORY.md",
             "README.md", "CLAUDE.md", "GLOSSARY.md"}

# Roots a cited (package-relative) path may live under, in priority order.
SEARCH_ROOTS = [REPO_ROOT, REPO_ROOT / "src" / "universal_agent", REPO_ROOT / "src"]


def _resolve_cited_file(fil: str) -> Path | None:
    """Resolve a cited path that may be package-relative (e.g. `durable/db.py` ->
    src/universal_agent/durable/db.py). Tries known roots, then a PATH-SUFFIX glob
    (not bare basename) so same-named files in other packages don't cause false hits."""
    fil = fil.strip().strip('"').strip("'")
    for root in SEARCH_ROOTS:
        cand = root / fil
        if cand.exists():
            return cand
    # suffix glob: match the full cited subpath, not just the basename
    matches = list(REPO_ROOT.glob(f"**/{fil}"))
    return matches[0] if matches else None


def _glob_resolves(glob: str) -> bool:
    """True if a code_paths glob resolves to >=1 existing path under any search root.
    Tolerates literal paths (incl. Next.js bracket routes like `[...path]`)."""
    glob = glob.strip().strip('"').strip("'")
    if not glob:
        return True
    has_magic = any(c in glob for c in "*?")  # NOTE: '[' excluded — Next.js route segments
    for root in SEARCH_ROOTS:
        if has_magic:
            try:
                if any(root.glob(glob)):
                    return True
            except (ValueError, OSError):
                pass
        if (root / glob).exists():
            return True
    return False

# Line-number citation anti-patterns (forbidden — they rot). Tuned to avoid common false positives.
LINE_NUM_PATTERNS = [
    re.compile(r"\.py:\d+"),                    # foo.py:123
    re.compile(r"\.(?:ts|tsx|js|jsx|yaml|yml):\d+"),
    re.compile(r"#L\d+"),                        # github-style #L123
    re.compile(r"\bL\d{2,}\b"),                  # L123
    re.compile(r"\blines?\s+\d+(?:\s*[-–]\s*\d+)?\b", re.IGNORECASE),  # "line 12", "lines 12-20"
]

# Symbol citation: `file.ext::symbol`  (inside backticks ideally, but match anywhere)
SYMBOL_REF = re.compile(r"`?([\w./-]+\.(?:py|ts|tsx|js|yaml|yml))::([A-Za-z_][\w.]*)`?")

# Markdown relative link
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@dataclass
class Finding:
    severity: str   # error | warn
    doc: str
    kind: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity, doc, kind, detail):
        self.findings.append(Finding(severity, doc, kind, detail))

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warns(self):
        return [f for f in self.findings if f.severity == "warn"]


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict-ish, body). Minimal YAML parse (no external deps)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip("\n")
    body = text[end + 4:]
    fm: dict = {}
    cur_key = None
    for line in raw.splitlines():
        if re.match(r"^\s*-\s+", line) and cur_key:           # list item
            fm.setdefault(cur_key, [])
            if isinstance(fm[cur_key], list):
                fm[cur_key].append(line.strip()[2:].strip())
            continue
        m = re.match(r"^([A-Za-z_][\w]*):\s*(.*)$", line)
        if m:
            cur_key, val = m.group(1), m.group(2).strip()
            fm[cur_key] = val if val else []
    return fm, body


def _doc_files() -> list[Path]:
    return sorted(p for p in DOCS_DIR.rglob("*.md") if "_archive" not in p.parts)


def check_frontmatter(path: Path, fm: dict, rep: Report):
    rel = str(path.relative_to(DOCS_DIR))
    for key in REQUIRED_FRONTMATTER:
        if key not in fm:
            rep.add("error", rel, "frontmatter", f"missing required key `{key}`")
    # code_paths globs should resolve to >=1 file (skip meta docs / empty code_paths)
    cps = fm.get("code_paths")
    if isinstance(cps, list):
        for glob in cps:
            glob = glob.strip().strip('"').strip("'")
            if not glob:
                continue
            if "::" in glob:
                rep.add("error", rel, "code_paths",
                        f"code_paths entry must be a file glob, not a symbol ref: `{glob}`")
                continue
            if not _glob_resolves(glob):
                rep.add("warn", rel, "code_paths", f"glob resolves to no files: `{glob}`")


def check_line_numbers(path: Path, body: str, rep: Report):
    rel = str(path.relative_to(DOCS_DIR))
    for pat in LINE_NUM_PATTERNS:
        for m in pat.finditer(body):
            # ignore inside fenced code blocks? keep simple: report, low noise expected
            rep.add("error", rel, "line_number_citation",
                    f"forbidden line-number citation `{m.group(0)}` — use file::symbol instead")
            break  # one report per pattern per doc is enough to flag


def check_symbol_refs(path: Path, body: str, rep: Report):
    rel = str(path.relative_to(DOCS_DIR))
    seen = set()
    for m in SYMBOL_REF.finditer(body):
        fil, sym = m.group(1), m.group(2)
        if (fil, sym) in seen:
            continue
        seen.add((fil, sym))
        target = _resolve_cited_file(fil)
        if target is None:
            rep.add("warn", rel, "symbol_ref", f"cited file not found: `{fil}` (::{sym})")
            continue
        try:
            content = target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        base = sym.split(".")[0]   # for Class.method, check Class or method token
        if not re.search(rf"\b{re.escape(base)}\b", content):
            rep.add("error", rel, "symbol_ref",
                    f"cited symbol `{fil}::{sym}` not found in file")


def check_links(path: Path, body: str, rep: Report):
    rel = str(path.relative_to(DOCS_DIR))
    for m in MD_LINK.finditer(body):
        tgt = m.group(1).split("#")[0].strip()
        if not tgt or tgt.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (path.parent / tgt).resolve()
        if not resolved.exists():
            rep.add("warn", rel, "broken_link", f"link target missing: `{tgt}`")


def check_orphans(rep: Report):
    if not README.exists():
        return  # README built in Phase 4
    index = README.read_text(encoding="utf-8", errors="ignore")
    for path in _doc_files():
        rel = str(path.relative_to(DOCS_DIR))
        if path.name in META_DOCS:
            continue
        if rel not in index and path.name not in index:
            rep.add("warn", rel, "orphan", "canonical doc not linked from README.md")


def run_audit(warn_only: bool) -> int:
    rep = Report()
    for path in _doc_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        fm, body = _split_frontmatter(text)
        if path.name in META_DOCS:
            # meta docs: only line-number + link checks
            check_line_numbers(path, body, rep)
            check_links(path, body, rep)
            continue
        check_frontmatter(path, fm, rep)
        check_line_numbers(path, body, rep)
        check_symbol_refs(path, body, rep)
        check_links(path, body, rep)
    check_orphans(rep)

    # print grouped report
    print(f"== doc_audit: {len(_doc_files())} docs scanned ==")
    print(f"   errors: {len(rep.errors)}   warnings: {len(rep.warns)}")
    for sev in ("error", "warn"):
        items = rep.errors if sev == "error" else rep.warns
        if not items:
            continue
        print(f"\n--- {sev.upper()}S ({len(items)}) ---")
        for f in items:
            print(f"  [{f.kind}] {f.doc}: {f.detail}")
    return 0 if (warn_only or not rep.errors) else 1


def build_accuracy_batch(n: int) -> list[dict]:
    """Return the n docs with the oldest last_verified, for the nightly LLM auditor."""
    rows = []
    for path in _doc_files():
        if path.name in META_DOCS:
            continue
        fm, _ = _split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
        rows.append({
            "doc": str(path.relative_to(DOCS_DIR)),
            "subsystem": fm.get("subsystem", ""),
            "code_paths": fm.get("code_paths", []),
            "last_verified": fm.get("last_verified", "0000-00-00"),
        })
    rows.sort(key=lambda r: r["last_verified"])
    return rows[:n]


def main():
    ap = argparse.ArgumentParser(description="project_docs documentation audit engine")
    ap.add_argument("--warn-only", action="store_true", help="report only; never exit nonzero")
    ap.add_argument("--accuracy-batch", type=int, default=0,
                    help="print the N oldest-verified docs as JSON (for the nightly LLM auditor)")
    args = ap.parse_args()
    if args.accuracy_batch:
        print(json.dumps(build_accuracy_batch(args.accuracy_batch), indent=2))
        return
    sys.exit(run_audit(args.warn_only))


if __name__ == "__main__":
    main()
