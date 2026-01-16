#!/usr/bin/env python3
"""
Recovery and Validation Script for Report Writer Agent

This script provides helpful diagnostics and recovery suggestions
when the report writing process encounters issues.
"""

import os
import json
from pathlib import Path
import sys

def check_workspace_structure(workspace_path: str) -> dict:
    """Validate workspace structure and provide diagnostics."""
    workspace = Path(workspace_path)
    status = {
        "workspace_exists": workspace.exists(),
        "outline_exists": False,
        "sections_dir_exists": False,
        "section_files": [],
        "refined_corpus_exists": False,
        "issues": [],
        "recovery_suggestions": []
    }
    
    # Check outline
    outline_path = workspace / "work_products/_working/outline.json"
    if outline_path.exists():
        status["outline_exists"] = True
        try:
            with open(outline_path) as f:
                outline_data = json.load(f)
                status["expected_sections"] = [s["id"] for s in outline_data.get("sections", [])]
        except Exception as e:
            status["issues"].append(f"Outline exists but couldn't parse: {e}")
    else:
        status["issues"].append("outline.json not found")
        status["recovery_suggestions"].append(
            "Agent needs to create outline.json before calling draft_report_parallel"
        )
    
    # Check sections directory
    sections_dir = workspace / "work_products/_working/sections"
    if sections_dir.exists():
        status["sections_dir_exists"] = True
        status["section_files"] = [f.name for f in sections_dir.glob("*.md")]
        
        if status["outline_exists"] and "expected_sections" in status:
            expected = set(f"{s}.md" for s in status["expected_sections"])
            actual = set(status["section_files"])
            missing = expected - actual
            extra = actual - expected
            
            if missing:
                status["issues"].append(f"Missing sections: {missing}")
                status["recovery_suggestions"].append(
                    f"Re-run draft_report_parallel or manually create: {missing}"
                )
            if extra:
                status["issues"].append(f"Unexpected sections: {extra}")
    else:
        status["issues"].append("Sections directory doesn't exist")
        if status["outline_exists"]:
            status["recovery_suggestions"].append(
                "Run draft_report_parallel to generate sections"
            )
    
    # Check refined corpus
    tasks_dir = workspace / "tasks"
    if tasks_dir.exists():
        corpus_files = list(tasks_dir.glob("*/refined_corpus.md"))
        if corpus_files:
            status["refined_corpus_exists"] = True
            status["refined_corpus_path"] = str(corpus_files[0])
        else:
            status["issues"].append("No refined_corpus.md found in tasks/")
    
    return status

def generate_recovery_script(status: dict, workspace_path: str) -> str:
    """Generate a recovery script based on diagnostics."""
    script_lines = ["#!/bin/bash", "# Recovery Script for Report Writer", ""]
    
    workspace = Path(workspace_path)
    
    if not status["outline_exists"]:
        script_lines.append("# ERROR: No outline.json found")
        script_lines.append("# Agent must create outline before proceeding")
        script_lines.append("exit 1")
    elif not status["sections_dir_exists"] or not status["section_files"]:
        script_lines.append("# Sections missing - re-run parallel drafting")
        script_lines.append(f"cd {workspace}")
        script_lines.append(f"python {Path(__file__).parent / 'parallel_draft.py'} {workspace}")
    elif len(status.get("section_files", [])) < len(status.get("expected_sections", [])):
        script_lines.append("# Some sections missing - re-run partial draft")
        script_lines.append(f"cd {workspace}")
        script_lines.append(f"python {Path(__file__).parent / 'parallel_draft.py'} {workspace}")
    else:
        script_lines.append("# All sections present - ready for compilation")
        script_lines.append(f"cd {workspace}")
        script_lines.append(f"python {Path(__file__).parent / 'compile_report.py'} --theme modern")
    
    return "\n".join(script_lines)

def main():
    if len(sys.argv) < 2:
        print("Usage: python report_diagnostics.py <workspace_path>")
        sys.exit(1)
    
    workspace_path = sys.argv[1]
    
    print("=== Report Writer Diagnostics ===\n")
    status = check_workspace_structure(workspace_path)
    
    print(f"Workspace: {workspace_path}")
    print(f"  ✓ Exists: {status['workspace_exists']}")
    print(f"  ✓ Outline: {status['outline_exists']}")
    print(f"  ✓ Sections Dir: {status['sections_dir_exists']}")
    print(f"  ✓ Section Files: {len(status['section_files'])}")
    print(f"  ✓ Refined Corpus: {status['refined_corpus_exists']}")
    
    if status["issues"]:
        print("\n⚠️  Issues Found:")
        for issue in status["issues"]:
            print(f"  - {issue}")
    
    if status["recovery_suggestions"]:
        print("\n💡 Recovery Suggestions:")
        for suggestion in status["recovery_suggestions"]:
            print(f"  - {suggestion}")
    
    if status["section_files"]:
        print("\n📄 Section Files Found:")
        for section in status["section_files"]:
            print(f"  - {section}")
    
    # Generate recovery script
    recovery_script = generate_recovery_script(status, workspace_path)
    recovery_path = Path(workspace_path) / "work_products/_working/recovery.sh"
    recovery_path.parent.mkdir(parents=True, exist_ok=True)
    recovery_path.write_text(recovery_script)
    recovery_path.chmod(0o755)
    
    print(f"\n✅ Recovery script saved: {recovery_path}")
    print("\nTo recover, run:")
    print(f"  bash {recovery_path}")
    
    # Output JSON for programmatic use
    if "--json" in sys.argv:
        print("\n" + json.dumps(status, indent=2))

if __name__ == "__main__":
    main()
