import asyncio
import json
import os
import sys
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic

from universal_agent.rate_limiter import ZAIRateLimiter

API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
MODEL = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5")


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
        "You are a professional report editor. Your goal is to maximize flow and remove redundancy.\n"
        "Review the full report sections and fix duplicated content across sections.\n\n"
        "CRITICAL RULES:\n"
        "1.  **Executive Summary Rewrite (MANDATORY)**: COMPLETELY REWRITE the 'Executive Summary' section. Do not use the provided draft. Instead, write a new summary that accurately synthesizes the key findings from the *other* sections you are reviewing. This ensures the summary matches the final report details.\n"
        "2.  **De-Duplication**: Remove statistics or facts that are repeated verbatim in multiple sections. Keep the detailed version in the most relevant section.\n"
        "3.  **Meta-Commentary Removal**: DELETE phrases like \"As mentioned in the previous section\", \"As noted above\", or \"In this section we will discuss\". The report should read as one continuous narrative.\n"
        "4.  **Acronyms**: Ensure acronyms are defined on first use (e.g., 'United Nations (UN)') and used consistently thereafter.\n"
        "5.  **Date Anchoring**: Replace relative time terms like 'yesterday' or 'last week' with specific dates (e.g., 'Jan 19') to ensure the report remains accurate over time.\n"
        "6.  **Transitions**: Add smooth transition sentences between abrupt topic shifts to improve flow.\n"
        "7.  **Placeholder Check**: If you see usage of \"[Insert ...]\" or \"TODO\", attempt to fix it or remove the sentence if no data is available.\n"
        "8.  **No Code Fences**: Do not wrap your output in markdown code blocks.\n"
        "9.  **Output Format**: Only return updates for sections that change.\n"
        "\n"
        "Return JSON only in this schema:\n"
        "{\"updates\": {\"filename.md\": \"<updated markdown>\"}, \"notes\": {\"filename.md\": [\"change summary\"]}}\n\n"
        "Sections:\n\n"
        + "\n\n".join(section_blocks)
    )


def extract_json_payload(text: str) -> Dict:
    # 1. Try to find markdown JSON block
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass  # Fallback to raw extraction

    # 2. Try raw JSON extraction
    start = text.find("{")
    end = text.rfind("}")
    
    if start == -1 or end == -1 or end <= start:
        # Check for potential truncation
        if start != -1 and end == -1:
             raise ValueError(f"JSON object appears truncated (found '{{' at {start} but no closing '}}'). Total length: {len(text)}")
        
        raise ValueError(f"No JSON object found in response. Length: {len(text)}")
        
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


def check_placeholders(text: str) -> List[str]:
    """Scan text for common placeholder patterns."""
    warnings = []
    # Patterns: [INSERT...], [TODO...], [STATS...], <INSERT...>
    regex = r"\[(INSERT|TODO|STATS|NOTE).*?\]|\[\s*\.\.\.\s*\]"
    matches = re.finditer(regex, text, re.IGNORECASE)
    for m in matches:
        warnings.append(f"Found placeholder: '{m.group(0)}'")
    return warnings


async def cleanup_report_async(workspace_path: Path) -> str:
    """
    Async entry point for cleaning up report sections.
    """
    if not API_KEY:
        return "Error: ANTHROPIC_AUTH_TOKEN/ZAI_API_KEY not set"

    if not workspace_path or not workspace_path.exists():
        return f"Error: Workspace not found at {workspace_path}"

    sections_dir = workspace_path / "work_products" / "_working" / "sections"
    if not sections_dir.exists():
        return f"Error: Sections directory not found at {sections_dir}"

    original_sections = load_sections(sections_dir)
    if not original_sections:
        return "Error: No sections found to clean"

    preprocessed_sections = preprocess_sections(original_sections)

    # Use centralized rate limiter with SDK retries disabled
    client = AsyncAnthropic(api_key=API_KEY, base_url=BASE_URL, max_retries=0, timeout=120.0)
    limiter = ZAIRateLimiter.get_instance()
    prompt = build_cleanup_prompt(preprocessed_sections)
    
    MAX_RETRIES = 5
    resp = None
    last_error = None
    context = "cleanup_report"
    
    for attempt in range(MAX_RETRIES):
        async with limiter.acquire(context):
            try:
                resp = await client.messages.create(
                    model=MODEL,
                    max_tokens=8192,
                    messages=[{"role": "user", "content": prompt}],
                )
                await limiter.record_success()
                break
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str or "high concurrency" in error_str
                
                if is_rate_limit:
                    await limiter.record_429(context)
                    last_error = e
                    
                    if attempt < MAX_RETRIES - 1:
                        delay = limiter.get_backoff(attempt)
                        print(f"  ⚠️ [429] Rate limited ({context}). Backoff: {delay:.1f}s (Attempt {attempt+1}/{MAX_RETRIES})")
                        await asyncio.sleep(delay)
                        continue
                else:
                    return f"Error during cleanup model call: {e}"
    
    if not resp:
        return f"Error: Cleanup failed after max retries. Last error: {last_error}"

    response_text = resp.content[0].text if resp.content else ""
    updates_payload: Dict[str, str] = {}
    notes_payload: Dict[str, List[str]] = {}

    if response_text.strip():
        try:
            parsed = extract_json_payload(response_text)
            updates_payload = parsed.get("updates", {}) or {}
            notes_payload = parsed.get("notes", {}) or {}
        except Exception as exc:
            return f"Error parsing cleanup response JSON: {exc}. Response preview: {response_text[:200]}"

    final_sections = dict(preprocessed_sections)
    updated_files: List[str] = []
    all_warnings: Dict[str, List[str]] = {}

    # Helper for fuzzy matching filenames
    def match_filename(target: str, available: List[str]) -> Optional[str]:
        # Normalize: lower, remove extension, remove non-alphanumeric
        def normalize(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower().replace(".md", ""))

        target_norm = normalize(target)
        
        # 1. Exact match (normalized)
        for fname in available:
            if normalize(fname) == target_norm:
                return fname
                
        # 2. Containment (e.g. "executive" in "01_executive_summary.md")
        # Check if target is in filename
        for fname in available:
            fname_norm = normalize(fname)
            if target_norm in fname_norm:
                return fname
        
        # 3. Reverse Containment (if LLM returns full path but we have short name?)
        for fname in available:
            fname_norm = normalize(fname)
            if fname_norm in target_norm:
                return fname
                
        return None

    for filename, content in updates_payload.items():
        matched_name = match_filename(filename, list(final_sections.keys()))
        if not matched_name:
            print(f"Warning: Could not match LLM file update '{filename}' to any local file. Skipping.")
            continue
            
        is_executive = "executive_summary" in matched_name
        cleaned = strip_wrapping_code_fence(content)
        cleaned = normalize_headings(cleaned, is_executive)
        
        # Validation
        warnings = check_placeholders(cleaned)
        if warnings:
            all_warnings[matched_name] = warnings

        final_sections[matched_name] = cleaned
        updated_files.append(matched_name)

    normalized_updates, _ = write_updates(
        sections_dir, original_sections, final_sections
    )

    summary_lines = ["✅ Cleanup complete."]
    if normalized_updates:
        summary_lines.append(f"Updated sections: {', '.join(sorted(normalized_updates))}.")
    
    if notes_payload:
        summary_lines.append("\nChange Notes:")
        for filename, notes in notes_payload.items():
            joined = "; ".join(notes)
            summary_lines.append(f"- {filename}: {joined}")
            
    if all_warnings:
        summary_lines.append("\n⚠️ VALIDATION WARNINGS:")
        for filename, warnings in all_warnings.items():
            joined = ", ".join(warnings)
            summary_lines.append(f"- {filename}: {joined}")

    return "\n".join(summary_lines)


async def main() -> int:
    # CLI Wrapper
    workspace = None
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1]).resolve()
    else:
        print("Error: No workspace path provided")
        return 1
    
    result = await cleanup_report_async(workspace)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
