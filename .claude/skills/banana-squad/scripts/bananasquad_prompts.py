#!/usr/bin/env python3
"""
Banana Squad (MVP): Prompt-only narrative prompt generator.

This script generates multiple prompt variations suitable for infographic generation tools.
It writes outputs into the active UA session workspace under `work_products/banana_squad/`.

Run:
  uv run .claude/skills/banana-squad/scripts/bananasquad_prompts.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Request:
    subject: str
    title: str | None = None
    style: str = "visual_capitalist_like"
    data_points: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    count: int = 5


STYLE_GUIDE = {
    # This is "VC-like" in the sense of data-first hierarchy and readability, not any exact copying.
    "visual_capitalist_like": {
        "background": "clean light background with subtle texture or soft gradient",
        "layout": "clear hierarchy: bold title, subtitle/context, 1 primary chart, 3-6 supporting callouts",
        "typography": "bold sans-serif title, clean sans-serif body, numbers in heavier weight",
        "palette": "restrained palette with 1-2 accent colors for emphasis; high contrast for readability",
        "tone": "authoritative, data-driven, editorial",
    },
    "dark_tech": {
        "background": "deep dark gradient background with subtle grid/circuit pattern",
        "layout": "centered hero element with structured cards around it; neon separators",
        "typography": "modern geometric sans-serif; strong contrast",
        "palette": "neon accents (cyan/magenta/green) on dark base",
        "tone": "futuristic, bold, tech-forward",
    },
    "minimal_clean": {
        "background": "pure white with generous whitespace",
        "layout": "grid-based modular layout; consistent padding and alignment",
        "typography": "light sans-serif; strong typographic scale",
        "palette": "neutral grays with one accent color",
        "tone": "premium, calm, uncluttered",
    },
    "corporate_blue": {
        "background": "navy or deep blue gradient",
        "layout": "structured columns with clear section headers and separators",
        "typography": "conservative sans-serif; chart labels optimized for legibility",
        "palette": "blues with gold/white accents",
        "tone": "trustworthy, established, boardroom-ready",
    },
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:60] or "run"


def _default_run_dir() -> Path:
    ws = os.environ.get("CURRENT_SESSION_WORKSPACE")
    if not ws:
        # Fallback for local ad-hoc runs.
        ws = str(Path.cwd())
    runs_root = Path(ws) / "work_products" / "banana_squad" / "runs"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return runs_root / ts


def _clean_sentence_list(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for it in items:
        it = (it or "").strip()
        if it:
            out.append(it)
    return out


def _build_prompt(req: Request, variation_idx: int, style_name: str) -> str:
    style = STYLE_GUIDE.get(style_name, STYLE_GUIDE["visual_capitalist_like"])

    title = req.title or req.subject
    dp = req.data_points[:8]
    constraints = req.constraints[:8]

    # Variation knobs: change chart framing + supporting elements while keeping style/legibility stable.
    primary_chart_options = [
        "a central bar chart comparing 4-6 categories",
        "a ranked list with horizontal bars and numeric labels",
        "a timeline with 4-8 milestones and short annotations",
        "a 2x2 quadrant chart with crisp labels and legend",
        "a single hero statistic with 3 supporting mini-charts",
    ]
    supporting_options = [
        "3 concise callout boxes summarizing key takeaways",
        "a small legend and a sources/notes footer area",
        "icon-supported bullet points with consistent stroke weight",
        "a side column of 'What it means' commentary in short sentences",
        "a bottom strip of related metrics with micro-charts",
    ]

    chart = primary_chart_options[(variation_idx - 1) % len(primary_chart_options)]
    supporting = supporting_options[(variation_idx - 1) % len(supporting_options)]

    parts: list[str] = []
    tone = style["tone"]
    article = "an" if tone[:1].lower() in ("a", "e", "i", "o", "u") else "a"
    parts.append(
        f"Create {article} {tone} infographic titled \"{title}\" about {req.subject}. "
        f"Use {style['layout']} on a {style['background']}. "
        f"Typography: {style['typography']}. Color palette: {style['palette']}."
    )
    parts.append(f"The main visualization should be {chart}. Include {supporting}.")

    if dp:
        parts.append(
            "Include these data points as on-chart labels or callouts (verbatim, with correct numbers): "
            + "; ".join(dp)
            + "."
        )
    if constraints:
        parts.append("Constraints: " + "; ".join(constraints) + ".")

    parts.append(
        "Prioritize readability: high contrast, no tiny text, consistent alignment, clear spacing, "
        "and a strong visual hierarchy. The result should look publication-ready."
    )

    # Single-paragraph narrative prompt (Gemini responds better to coherent prose).
    prompt = " ".join(parts)
    prompt = " ".join(prompt.split())
    return prompt.strip()


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Banana Squad prompt-only generator (MVP).")
    parser.add_argument("--subject", required=True, help="What the graphic is about.")
    parser.add_argument("--title", default=None, help="Infographic title (optional).")
    parser.add_argument(
        "--style",
        default="visual_capitalist_like",
        choices=sorted(STYLE_GUIDE.keys()),
        help="Style preset to use.",
    )
    parser.add_argument("--data", action="append", default=[], help="Repeatable data points to include verbatim.")
    parser.add_argument("--constraint", action="append", default=[], help="Repeatable constraints.")
    parser.add_argument("--count", type=int, default=5, help="Number of prompt variations (1-10).")
    parser.add_argument("--out-dir", default=None, help="Override output dir (defaults to session run dir).")

    args = parser.parse_args()

    count = max(1, min(int(args.count), 10))
    req = Request(
        subject=args.subject.strip(),
        title=(args.title.strip() if args.title else None),
        style=args.style,
        data_points=_clean_sentence_list(args.data),
        constraints=_clean_sentence_list(args.constraint),
        count=count,
    )

    run_dir = Path(args.out_dir) if args.out_dir else _default_run_dir()

    prompts = []
    for i in range(1, count + 1):
        prompts.append(
            {
                "id": f"p{i}",
                "style": req.style,
                "prompt": _build_prompt(req, i, req.style),
            }
        )

    out = {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "request": asdict(req),
        "prompts": prompts,
    }

    _write_json(run_dir / "request.json", asdict(req))
    _write_json(run_dir / "prompts.json", out)

    md_lines = [f"# Banana Squad Prompts", "", f"Generated: `{out['generated_at']}`", ""]
    md_lines.append("## Request")
    md_lines.append("```json")
    md_lines.append(json.dumps(asdict(req), indent=2))
    md_lines.append("```")
    md_lines.append("")
    md_lines.append("## Variations")
    for p in prompts:
        md_lines.append(f"### {p['id']}")
        md_lines.append("")
        md_lines.append(p["prompt"])
        md_lines.append("")
    _write_text(run_dir / "prompts.md", "\n".join(md_lines).rstrip() + "\n")

    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
