"""
Generate project_docs/README.md — the SINGLE documentation index.

Replaces the legacy dual-index (README.md + Documentation_Status.md). The index is
*generated* from the doc manifest + each doc's frontmatter, so it can never drift from
disk: a doc that exists is listed; "last updated" comes from `last_verified` frontmatter,
not a hand-maintained changelog.

Run after reconstruction (and in CI to detect staleness):
    python scripts/gen_doc_index.py            # write project_docs/README.md
    python scripts/gen_doc_index.py --check     # exit 1 if README is out of date
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

REPO_ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, check=True).stdout.strip())
DOCS = REPO_ROOT / "project_docs"
MANIFEST = DOCS / "_meta" / "doc_manifest.json"
README = DOCS / "README.md"

META_ORDER = [
    ("00_DOCUMENTATION_REFACTOR_PLAN.md", "How and why the docs were rebuilt (code-first)."),
    ("01_TAXONOMY.md", "Category structure and the canonical doc set."),
    ("02_GOTCHA_INVENTORY.md", "Preserved operational/rationale facts not visible in code."),
    ("GLOSSARY.md", "Project-specific terminology."),
    ("CLAUDE.md", "Documentation governance (rules) — lazy-loaded when editing docs."),
]


def read_frontmatter(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_]\w*):\s*(.*)$", line)
        if m and m.group(2).strip():
            fm[m.group(1)] = m.group(2).strip()
    return fm


def build_index() -> str:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cats = manifest["categories"]
    docs = manifest["docs"]

    lines = [
        "# Universal Agent Documentation",
        "",
        "Single source of truth for the rebuilt documentation. **This index is generated**",
        "(`scripts/gen_doc_index.py`) from the doc manifest + each doc's frontmatter — it cannot",
        "drift from disk. Editing rules live in [`CLAUDE.md`](CLAUDE.md) and are enforced by CI",
        "(`scripts/doc_audit.py`).",
        "",
        "> **Code is the source of truth.** Every doc is reconstructed from code, cites `file::symbol`",
        "> (never line numbers), and carries `code_paths` frontmatter that drives PR-time drift checks.",
        "",
        "## Meta",
        "",
    ]
    for fname, desc in META_ORDER:
        if (DOCS / fname).exists():
            lines.append(f"- [{fname}]({fname}) — {desc}")
    lines.append("")

    by_cat: dict[str, list[dict]] = {}
    for d in docs:
        by_cat.setdefault(d["category"], []).append(d)

    for cat in sorted(cats):
        entries = sorted(by_cat.get(cat, []), key=lambda d: d["filename"])
        present = [d for d in entries if (DOCS / d["filename"]).exists()]
        if not present:
            continue
        lines.append(f"## {cat}")
        lines.append("")
        lines.append(f"_{cats[cat]}_")
        lines.append("")
        for d in present:
            fm = read_frontmatter(DOCS / d["filename"])
            rel = d["filename"]
            title = fm.get("title", d["title"])
            lv = fm.get("last_verified", "")
            scope = d.get("scope", "")
            # trim scope to one line
            scope = re.split(r"(?<=[.)])\s", scope)[0] if scope else ""
            suffix = f" _(verified {lv})_" if lv else ""
            lines.append(f"- **[{title}]({rel})** — {scope}{suffix}")
        lines.append("")

    lines.append("---")
    lines.append("")
    total = sum(1 for d in docs if (DOCS / d["filename"]).exists())
    lines.append(f"_{total}/{len(docs)} canonical docs present. Legacy point-in-time reports are archived "
                 f"(search-excluded) — see `00_DOCUMENTATION_REFACTOR_PLAN.md` §5._")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if README.md is stale")
    args = ap.parse_args()
    content = build_index()
    if args.check:
        current = README.read_text(encoding="utf-8") if README.exists() else ""
        if current != content:
            print("project_docs/README.md is OUT OF DATE — run scripts/gen_doc_index.py")
            sys.exit(1)
        print("README.md up to date.")
        return
    README.write_text(content, encoding="utf-8")
    print(f"Wrote {README.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
