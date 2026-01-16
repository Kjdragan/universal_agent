from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

from anthropic import Anthropic
from markdown import markdown

from rom_env import CorpusEnv


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, **kwargs: str) -> str:
    rendered = template
    for key, value in kwargs.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_client(env_path: Path | None = None) -> Anthropic:
    if env_path:
        load_env_file(env_path)
    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN/ZAI_API_KEY)")

    client_kwargs: Dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    return Anthropic(**client_kwargs)


def call_llm(client: Anthropic, model: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
    response = client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
    return response.content[0].text


def extract_json(text: str) -> Dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(line for line in cleaned.splitlines() if not line.strip().startswith("```"))
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def build_corpus_overview(index: List[Dict[str, str]], max_items: int) -> str:
    dates = [item.get("date", "unknown") for item in index if item.get("date") not in ("unknown", "")]
    date_range = "unknown"
    if dates:
        date_range = f"{min(dates)} to {max(dates)}"

    lines = [
        f"Corpus size: {len(index)} files",
        f"Date range: {date_range}",
        "Sample files:",
    ]
    for item in index[:max_items]:
        lines.append(
            f"- {item.get('title', 'Untitled')} | {item.get('date', 'unknown')} | {item.get('path')}")
    return "\n".join(lines)


def safe_json_dumps(data: Any, max_chars: int = 6000) -> str:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if len(payload) <= max_chars:
        return payload
    return payload[: max_chars - 3] + "..."


def run_planner(
    client: Anthropic,
    model: str,
    planner_prompt: str,
    topic: str,
    corpus_overview: str,
    max_tokens: int,
    max_retries: int = 2,
    log_event: Callable[[str], None] | None = None,
) -> Dict[str, Any]:
    messages = [
        {"role": "user", "content": render_prompt(planner_prompt, topic=topic, corpus_overview=corpus_overview)}
    ]
    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        response = call_llm(client, model, messages, max_tokens=max_tokens)
        if log_event:
            log_event("planner_response=" + response)
        plan = extract_json(response)
        if plan:
            return plan
        messages.append({"role": "assistant", "content": response})
        messages.append(
            {
                "role": "user",
                "content": "The previous response was not valid JSON. Return ONLY the JSON object matching the schema.",
            }
        )
    raise RuntimeError("Planner did not return valid JSON")


def run_explorer(
    client: Anthropic,
    model: str,
    explorer_prompt: str,
    env: CorpusEnv,
    section: Dict[str, Any],
    config: Dict[str, Any],
    log_event: Callable[[str], None] | None = None,
) -> List[Dict[str, Any]]:
    messages = [
        {"role": "user", "content": render_prompt(explorer_prompt, section_json=json.dumps(section, indent=2))}
    ]
    evidence: List[Dict[str, Any]] = []
    fallback_evidence: List[Dict[str, Any]] = []
    completed = False
    evidence_limit = int(config.get("evidence_limit", 8))
    max_per_source = int(config.get("max_evidence_per_source", 3))

    def capture_match(match: Dict[str, Any]) -> None:
        fallback_evidence.append(
            {
                "claim": match.get("title") or "Search evidence",
                "snippet": match.get("snippet", ""),
                "source_path": match.get("path", ""),
                "source_url": match.get("source", ""),
                "date": match.get("date", "unknown"),
                "notes": "search match",
            }
        )

    def capture_read(result: Dict[str, Any]) -> None:
        metadata = result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {}
        body = str(result.get("body", ""))
        snippet = body.replace("\n", " ")[:400] if body else ""
        fallback_evidence.append(
            {
                "claim": metadata.get("title") or "File excerpt",
                "snippet": snippet,
                "source_path": result.get("path", ""),
                "source_url": metadata.get("source", ""),
                "date": metadata.get("date", "unknown"),
                "notes": "read_file excerpt",
            }
        )

    def normalize_snippet(snippet: str) -> str:
        cleaned = " ".join(str(snippet).split())
        lowered = cleaned.lower()
        if not cleaned:
            return ""
        if cleaned.startswith("---"):
            return ""
        if lowered.startswith("opens in a new window"):
            return ""
        if "we use essential cookies" in lowered or "we use cookies" in lowered:
            return ""
        if "privacy policy" in lowered or "cookie policy" in lowered:
            return ""
        if "screen-reader mode" in lowered:
            return ""
        if is_boilerplate(cleaned):
            return ""
        return cleaned

    def is_boilerplate(snippet: str) -> bool:
        lowered = snippet.lower()
        if "home |" in lowered or lowered.startswith("home"):
            return True
        if any(
            phrase in lowered
            for phrase in (
                "facebook",
                "twitter",
                "linkedin",
                "instagram",
                "youtube",
                "tiktok",
                "sign up",
                "subscribe",
                "newsletter",
                "all rights reserved",
                "terms of service",
                "contact us",
                "follow us",
            )
        ):
            return True
        url_count = len(re.findall(r"https?://", snippet))
        if url_count >= 2:
            return True
        if snippet.count("](") >= 2:
            return True
        alpha_chars = sum(1 for char in snippet if char.isalpha())
        alpha_ratio = alpha_chars / max(len(snippet), 1)
        if alpha_ratio < 0.55:
            return True
        return False

    def finalize_evidence(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        seen = set()
        for item in items:
            snippet = normalize_snippet(item.get("snippet", ""))
            if not snippet or len(snippet) < 80:
                continue
            source_path = item.get("source_path", "") or "unknown"
            key = (source_path, snippet)
            if key in seen:
                continue
            seen.add(key)
            cleaned_item = dict(item)
            cleaned_item["snippet"] = snippet
            bucket = buckets.setdefault(source_path, [])
            if len(bucket) < max_per_source:
                bucket.append(cleaned_item)

        selected: List[Dict[str, Any]] = []
        while buckets and len(selected) < limit:
            for key in list(buckets.keys()):
                if not buckets.get(key):
                    buckets.pop(key, None)
                    continue
                selected.append(buckets[key].pop(0))
                if len(selected) >= limit:
                    break
        return selected
    max_steps = int(config.get("explorer_max_steps", 8))
    for step in range(max_steps):
        response = call_llm(
            client,
            model,
            messages,
            max_tokens=int(config.get("explorer_max_tokens", 1200)),
        )
        if log_event:
            log_event(f"explorer_step={step + 1} response={response}")
        action = extract_json(response)
        if not action:
            messages.append({"role": "assistant", "content": response})
            messages.append(
                {
                    "role": "user",
                    "content": "The previous response was not valid JSON. Return a JSON action only.",
                }
            )
            continue

        if action.get("action") == "final":
            raw_evidence = action.get("evidence", [])
            evidence = finalize_evidence(raw_evidence, limit=evidence_limit)
            if log_event:
                log_event(
                    f"explorer_final_evidence_count={len(evidence)} raw={len(raw_evidence)}"
                )
            completed = True
            break

        tool_result = env.execute_action(action, config)
        if log_event:
            log_event("explorer_action=" + json.dumps(action, ensure_ascii=False))
            log_event("explorer_tool_result=" + safe_json_dumps(tool_result))
        if isinstance(tool_result, dict):
            if "matches" in tool_result:
                for match in tool_result.get("matches", []) or []:
                    if isinstance(match, dict):
                        capture_match(match)
            if "body" in tool_result and "path" in tool_result:
                capture_read(tool_result)
        messages.append({"role": "assistant", "content": json.dumps(action, indent=2)})
        messages.append(
            {
                "role": "user",
                "content": f"Tool result:\n{safe_json_dumps(tool_result)}",
            }
        )

    if not completed and fallback_evidence:
        filtered = finalize_evidence(fallback_evidence, limit=evidence_limit)
        if log_event:
            log_event(
                f"explorer_fallback_evidence_count={len(filtered)} raw={len(fallback_evidence)}"
            )
        return filtered
    if completed and not evidence and fallback_evidence:
        filtered = finalize_evidence(fallback_evidence, limit=evidence_limit)
        if log_event:
            log_event(
                f"explorer_fallback_after_final={len(filtered)} raw={len(fallback_evidence)}"
            )
        return filtered
    return evidence


def run_section_writer(
    client: Anthropic,
    model: str,
    section_prompt: str,
    section: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    max_tokens: int,
    log_event: Callable[[str], None] | None = None,
) -> str:
    content = render_prompt(
        section_prompt,
        section_title=str(section.get("title", "Section")),
        section_focus=str(section.get("focus", "")),
        evidence_json=safe_json_dumps(evidence, max_chars=12000),
    )
    response = call_llm(client, model, [{"role": "user", "content": content}], max_tokens=max_tokens)
    if log_event:
        log_event(f"section_writer={section.get('id','section')} response={response}")
    return response.strip()


def run_summary(
    client: Anthropic,
    model: str,
    summary_prompt: str,
    report_title: str,
    thesis: str,
    section_summaries: List[str],
    max_tokens: int,
    log_event: Callable[[str], None] | None = None,
) -> str:
    content = render_prompt(
        summary_prompt,
        report_title=report_title,
        thesis=thesis,
        section_summaries="\n".join(section_summaries),
    )
    response = call_llm(client, model, [{"role": "user", "content": content}], max_tokens=max_tokens)
    if log_event:
        log_event("summary_response=" + response)
    return response.strip()


def build_sources(evidence: List[Dict[str, Any]], env: CorpusEnv) -> str:
    seen = set()
    lines = ["## Sources"]
    for item in evidence:
        path = item.get("source_path") or item.get("path")
        if not path or path in seen:
            continue
        seen.add(path)
        meta = env.get_metadata(path)
        title = meta.get("title", path)
        url = meta.get("source", "")
        date = meta.get("date", "unknown")
        lines.append(f"- {title} ({date}) {url} [{path}]")
    return "\n".join(lines)


def render_html(report_md: str, report_title: str) -> str:
    body = markdown(report_md, extensions=["tables", "fenced_code"])
    timestamp = datetime.utcnow().strftime("%Y-%m-%d")
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{report_title}</title>
  <style>
    :root {{
      --fg: #1c1c1c;
      --muted: #5f5f5f;
      --accent: #0f3d5e;
      --bg: #ffffff;
    }}
    body {{
      margin: 0;
      font-family: "Georgia", "Times New Roman", serif;
      color: var(--fg);
      background: var(--bg);
    }}
    header {{
      padding: 32px 8vw 16px;
      border-bottom: 1px solid #e6e6e6;
    }}
    header h1 {{
      margin: 0 0 8px;
      font-size: 2.1rem;
      color: var(--accent);
    }}
    header p {{
      margin: 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    main {{
      padding: 24px 8vw 64px;
      line-height: 1.6;
    }}
    h2 {{
      color: var(--accent);
      margin-top: 2.2rem;
    }}
    h3 {{
      color: var(--accent);
      margin-top: 1.6rem;
    }}
    blockquote {{
      margin: 1.2rem 0;
      padding: 0.6rem 1rem;
      background: #f6f8fa;
      border-left: 4px solid #d0d7de;
    }}
    ul {{
      padding-left: 1.2rem;
    }}
    .meta {{
      font-size: 0.9rem;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <header>
    <h1>{report_title}</h1>
    <p class=\"meta\">Generated {timestamp}</p>
  </header>
  <main>
    {body}
  </main>
</body>
</html>"""


def assemble_report(title: str, summary_md: str, sections: List[str], sources_md: str) -> str:
    parts = [f"# {title}", "", summary_md, ""]
    parts.extend(sections)
    parts.append("")
    parts.append(sources_md)
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="RLM_Exploration/config.json")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config_dir = config_path.parent
    config = load_json(config_path)

    corpus_dir = resolve_path(config_dir.parent, str(config.get("corpus_dir")))
    output_dir = resolve_path(config_dir.parent, str(config.get("output_dir")))
    output_dir.mkdir(parents=True, exist_ok=True)
    sections_dir = output_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "run.log"

    def log_event(message: str) -> None:
        timestamp = datetime.utcnow().isoformat()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    def stage(message: str) -> None:
        print(f"[RLM] {message}")
        log_event(f"stage={message}")

    env = CorpusEnv(corpus_dir)
    corpus_overview = build_corpus_overview(env.index, int(config.get("index_preview_items", 20)))

    stage("Starting run")
    stage(f"Corpus loaded: {len(env.index)} documents")

    prompts_dir = config_dir / "prompts"
    planner_prompt = read_prompt(prompts_dir / "planner.md")
    explorer_prompt = read_prompt(prompts_dir / "explorer.md")
    section_prompt = read_prompt(prompts_dir / "section_writer.md")
    summary_prompt = read_prompt(prompts_dir / "assembler.md")

    client = get_client(config_dir.parent / ".env")
    model = str(config.get("model"))

    plan = run_planner(
        client,
        model,
        planner_prompt,
        str(config.get("topic")),
        corpus_overview,
        int(config.get("planner_max_tokens", 1200)),
        max_retries=int(config.get("planner_max_retries", 2)),
        log_event=log_event,
    )
    stage(f"Planner complete: {len(plan.get('sections', []))} sections")

    outline_path = output_dir / "outline.json"
    outline_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

    evidence_path = output_dir / "evidence.jsonl"
    all_evidence: List[Dict[str, Any]] = []
    sections_text: List[str] = []

    for section in plan.get("sections", []):
        section_id = section.get("id", "section")
        section_title = section.get("title", "Section")
        stage(f"Explorer start: {section_id} - {section_title}")
        section_evidence = run_explorer(client, model, explorer_prompt, env, section, config, log_event)
        stage(f"Explorer done: {section_id} - evidence items={len(section_evidence)}")

        if not section_evidence:
            stage(f"WARNING: No evidence for {section_id} - {section_title}; inserting placeholder")
            placeholder = f"## {section_title}\n\n> No evidence collected for this section. Skipped.\n"
            section_file = sections_dir / f"section_{section_id}.md"
            section_file.write_text(placeholder, encoding="utf-8")
            sections_text.append(placeholder)
            continue

        for item in section_evidence:
            item.setdefault("section_id", section_id)
        all_evidence.extend(section_evidence)

        section_text = run_section_writer(
            client,
            model,
            section_prompt,
            section,
            section_evidence,
            int(config.get("section_max_tokens", 1600)),
            log_event,
        )
        section_file = sections_dir / f"section_{section_id}.md"
        section_file.write_text(section_text, encoding="utf-8")
        sections_text.append(section_text)
        stage(f"Section written: {section_id}")

    if not all_evidence:
        stage("ERROR: No evidence collected across all sections. Aborting report generation.")
        return

    with evidence_path.open("w", encoding="utf-8") as handle:
        for item in all_evidence:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    stage(f"Evidence written: {evidence_path}")

    sources_md = build_sources(all_evidence, env)
    sources_path = output_dir / "sources.md"
    sources_path.write_text(sources_md, encoding="utf-8")
    stage(f"Sources written: {sources_path}")

    section_summaries = []
    for section_text in sections_text:
        cleaned = re.sub(r"\s+", " ", section_text)
        section_summaries.append(cleaned[:500])

    summary_md = run_summary(
        client,
        model,
        summary_prompt,
        str(plan.get("title", config.get("report_title"))),
        str(plan.get("thesis", "")),
        section_summaries,
        int(config.get("summary_max_tokens", 700)),
        log_event,
    )
    stage("Summary written")

    report_md = assemble_report(
        str(plan.get("title", config.get("report_title"))),
        summary_md,
        sections_text,
        sources_md,
    )
    report_path = output_dir / "report.md"
    report_path.write_text(report_md, encoding="utf-8")

    html = render_html(report_md, str(plan.get("title", config.get("report_title"))))
    html_path = output_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")

    stage(f"Report complete: {report_path}")
    stage(f"HTML report: {html_path}")


if __name__ == "__main__":
    main()
