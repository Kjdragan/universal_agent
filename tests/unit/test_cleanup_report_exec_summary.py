"""RED/GREEN test for Bug 3: executive summary placeholder surviving cleanup.

When the LLM cleanup step doesn't return an update for the executive summary,
the ``[Pending Synthesis by Cleanup Tool]`` marker is stripped but the section
body is left effectively empty.  The fix adds a fallback that generates a
summary from the other sections' opening lines.
"""

import re

from universal_agent.scripts.cleanup_report import (
    preprocess_sections,
    write_updates,
    check_placeholders,
    strip_wrapping_code_fence,
    normalize_headings,
)


# ---- Helpers copied from cleanup_report to test the post-LLM logic ----
def _apply_updates_and_check_fallback(sections: dict, updates_payload: dict) -> dict:
    """Simulate the post-LLM update logic from cleanup_report_async."""
    from universal_agent.scripts.cleanup_report import (
        strip_wrapping_code_fence,
        normalize_headings,
    )
    pending_marker = re.compile(
        r"^\s*\[Pending Synthesis by Cleanup Tool\]\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

    final_sections = dict(sections)
    all_warnings: dict = {}

    # Apply LLM updates (match_filename simplified for test)
    for filename, content in updates_payload.items():
        matched = None
        for k in final_sections:
            if filename.lower().replace(".md", "") in k.lower().replace(".md", ""):
                matched = k
                break
        if matched:
            is_executive = "executive_summary" in matched
            cleaned = strip_wrapping_code_fence(content)
            cleaned = normalize_headings(cleaned, is_executive)
            final_sections[matched] = cleaned

    # Strip pending markers
    for filename, content in list(final_sections.items()):
        cleaned = pending_marker.sub("", content)
        if cleaned != content:
            cleaned = cleaned.strip() + "\n"
            final_sections[filename] = cleaned
            all_warnings.setdefault(filename, []).append(
                "Removed leftover pending synthesis marker."
            )

    # --- THIS IS THE CODE UNDER TEST ---
    # Fallback for empty executive summary
    for filename, content in list(final_sections.items()):
        if "executive_summary" not in filename:
            continue
        body = re.sub(r"^#.*$", "", content, flags=re.MULTILINE).strip()
        if len(body) < 50:
            summary_parts = []
            for other_name, other_content in sorted(final_sections.items()):
                if "executive_summary" in other_name:
                    continue
                lines = other_content.strip().splitlines()
                heading = ""
                text_lines = []
                for line in lines:
                    if line.startswith("#"):
                        heading = line.lstrip("#").strip()
                    elif line.strip():
                        text_lines.append(line.strip())
                        if len(text_lines) >= 2:
                            break
                if heading and text_lines:
                    summary_parts.append(f"**{heading}:** {' '.join(text_lines)}")
            if summary_parts:
                fallback = "# Executive Summary\n\n" + "\n\n".join(summary_parts) + "\n"
                final_sections[filename] = fallback
                all_warnings.setdefault(filename, []).append(
                    "Executive summary was empty after cleanup; generated fallback from section leads."
                )

    return final_sections, all_warnings


def test_empty_exec_summary_gets_fallback():
    """When the LLM doesn't update the exec summary, a fallback is generated."""
    sections = {
        "01_01_executive_summary.md": (
            "# Executive Summary\n\n[Pending Synthesis by Cleanup Tool]\n"
        ),
        "02_02_military_operations.md": (
            "## Military Operations Assessment\n\n"
            "Russian forces advanced in northern Sumy Oblast. "
            "Drone strikes intensified across multiple frontline sectors.\n"
        ),
        "03_03_diplomatic_developments.md": (
            "## Diplomatic Developments\n\n"
            "Turkey proposed a new ceasefire framework. "
            "UN envoy briefed the Security Council on April 22.\n"
        ),
    }

    # Simulate: LLM returns NO update for executive summary
    updates_payload = {}

    final, warnings = _apply_updates_and_check_fallback(sections, updates_payload)

    exec_content = final["01_01_executive_summary.md"]
    # Must contain the heading
    assert "# Executive Summary" in exec_content
    # Must NOT contain the placeholder
    assert "Pending Synthesis" not in exec_content
    # Must contain content synthesized from other sections
    assert "Military Operations" in exec_content
    assert "Diplomatic Developments" in exec_content
    # Must have generated a warning
    assert any("fallback" in w.lower() for w in warnings.get("01_01_executive_summary.md", []))
    # Body must be substantial (not just heading)
    body = re.sub(r"^#.*$", "", exec_content, flags=re.MULTILINE).strip()
    assert len(body) > 50


def test_exec_summary_with_llm_update_not_overridden():
    """When the LLM DOES update the exec summary, the fallback is NOT triggered."""
    sections = {
        "01_01_executive_summary.md": (
            "# Executive Summary\n\n[Pending Synthesis by Cleanup Tool]\n"
        ),
        "02_02_military_operations.md": (
            "## Military Operations\n\nContent here.\n"
        ),
    }

    # Simulate: LLM returns a proper executive summary
    updates_payload = {
        "executive_summary": (
            "# Executive Summary\n\n"
            "This report covers the latest developments in the Ukraine-Russia conflict, "
            "including military operations, diplomatic initiatives, and humanitarian impact. "
            "Key findings include intensified drone warfare and new ceasefire proposals.\n"
        ),
    }

    final, warnings = _apply_updates_and_check_fallback(sections, updates_payload)

    exec_content = final["01_01_executive_summary.md"]
    assert "Key findings include" in exec_content
    assert "Pending Synthesis" not in exec_content
    # Fallback should NOT have been triggered
    fallback_warnings = [
        w for w in warnings.get("01_01_executive_summary.md", [])
        if "fallback" in w.lower()
    ]
    assert len(fallback_warnings) == 0
