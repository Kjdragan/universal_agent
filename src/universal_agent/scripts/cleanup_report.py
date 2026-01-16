import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from anthropic import AsyncAnthropic

API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
MODEL = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-4.7")


def strip_wrapping_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text

    lines = stripped.splitlines()
    if len(lines) < 2:
        return text

    if lines[0].startswith("```") and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip() + "\n"

    return text


def normalize_headings(text: str, is_executive: bool) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return text

    normalized: List[str] = []
    exec_heading_set = False

    for line in lines:
        if line.startswith("# "):
            if is_executive:
                if not exec_heading_set:
                    normalized.append("# Executive Summary")
                    exec_heading_set = True
                else:
                    normalized.append("## " + line[2:])
            else:
                normalized.append("## " + line[2:])
            continue

        if is_executive and not exec_heading_set and line.startswith("## "):
            normalized.append("# Executive Summary")
            exec_heading_set = True
            continue

        normalized.append(line)

    if is_executive and not exec_heading_set:
        normalized.insert(0, "# Executive Summary")

    return "\n".join(normalized).strip() + "\n"


def preprocess_sections(sections: Dict[str, str]) -> Dict[str, str]:
    processed: Dict[str, str] = {}
    for filename, content in sections.items():
        is_executive = "executive_summary" in filename
        content = strip_wrapping_code_fence(content)
        content = normalize_headings(content, is_executive)
        processed[filename] = content
    return processed


def build_cleanup_prompt(sections: Dict[str, str]) -> str:
    section_blocks = []
    for filename, content in sections.items():
        section_blocks.append(
            f"=== FILE: {filename} ===\n{content}\n=== END FILE ==="
        )

    return (
        "You are a report editor. Review the full report sections and fix formatting "
        "inconsistencies and duplicated content across sections. Make targeted edits "
        "only; do not rewrite the entire report.\n\n"
        "Rules:\n"
        "- Do not add new facts.\n"
        "- Remove or condense repeated stats across sections; keep detailed numbers "
        "in the most relevant section and keep the executive summary high-level.\n"
        "- Ensure heading hierarchy: Executive Summary uses '# Executive Summary'. "
        "All other sections start with '## <Section Title>' and use '###' for subheads.\n"
        "- Do not wrap output in code fences.\n"
        "- Only return updates for sections that need changes.\n\n"
        "Return JSON only in this schema:\n"
        "{\"updates\": {\"filename.md\": \"<updated markdown>\"}, \"notes\": {\"filename.md\": [\"change summary\"]}}\n\n"
        "Sections:\n\n"
        + "\n\n".join(section_blocks)
    )


def extract_json_payload(text: str) -> Dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in response")
    payload = text[start : end + 1]
    return json.loads(payload)


def load_sections(sections_dir: Path) -> Dict[str, str]:
    md_files = sorted(sections_dir.glob("*.md"))
    return {path.name: path.read_text(encoding="utf-8") for path in md_files}


def write_updates(
    sections_dir: Path,
    original_sections: Dict[str, str],
    final_sections: Dict[str, str],
) -> Tuple[List[str], List[str]]:
    normalized_updates: List[str] = []
    llm_updates: List[str] = []

    for filename, content in final_sections.items():
        if filename not in original_sections:
            continue

        if content != original_sections[filename]:
            (sections_dir / filename).write_text(content, encoding="utf-8")
            normalized_updates.append(filename)

    return normalized_updates, llm_updates


async def main() -> int:
    if not API_KEY:
        print("Error: ANTHROPIC_AUTH_TOKEN not set")
        return 1

    workspace = None
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1]).resolve()
    if not workspace or not workspace.exists():
        print("Error: Workspace not found or not a directory")
        return 1

    sections_dir = workspace / "work_products" / "_working" / "sections"
    if not sections_dir.exists():
        print(f"Error: Sections directory not found at {sections_dir}")
        return 1

    original_sections = load_sections(sections_dir)
    if not original_sections:
        print("Error: No sections found to clean")
        return 1

    preprocessed_sections = preprocess_sections(original_sections)

    client = AsyncAnthropic(api_key=API_KEY, base_url=BASE_URL)
    prompt = build_cleanup_prompt(preprocessed_sections)

    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"Error during cleanup model call: {exc}")
        return 1

    response_text = resp.content[0].text if resp.content else ""
    updates_payload: Dict[str, str] = {}
    notes_payload: Dict[str, List[str]] = {}

    if response_text.strip():
        try:
            parsed = extract_json_payload(response_text)
            updates_payload = parsed.get("updates", {}) or {}
            notes_payload = parsed.get("notes", {}) or {}
        except Exception as exc:
            print(f"Error parsing cleanup response JSON: {exc}")
            return 1

    final_sections = dict(preprocessed_sections)
    updated_files: List[str] = []

    for filename, content in updates_payload.items():
        if filename not in final_sections:
            continue
        is_executive = "executive_summary" in filename
        cleaned = strip_wrapping_code_fence(content)
        cleaned = normalize_headings(cleaned, is_executive)
        final_sections[filename] = cleaned
        updated_files.append(filename)

    normalized_updates, _ = write_updates(
        sections_dir, original_sections, final_sections
    )

    summary_lines = ["Cleanup complete."]
    if normalized_updates:
        summary_lines.append(f"Updated sections: {', '.join(sorted(normalized_updates))}.")
    if notes_payload:
        summary_lines.append("Notes:")
        for filename, notes in notes_payload.items():
            joined = "; ".join(notes)
            summary_lines.append(f"- {filename}: {joined}")

    print("\n".join(summary_lines))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
