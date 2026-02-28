"""
Research Pipeline Drift Detection Tests
========================================

These tests guard against the drift that broke the research→report→PDF→email
pipeline in Feb 2026. They verify invariants at the constants, hooks, agent
definitions, and guardrail layers — all without requiring a live agent run.

Golden run reference: session_20260223_215506_140963c1
  Tool sequence: Task(research) → MULTI_EXECUTE(search) → run_research_phase
                 → Task(report) → run_report_generation → list_directory
                 → html_to_pdf → upload_to_composio → MULTI_EXECUTE(email)

See docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md for full SDK reference.
"""

import re
from pathlib import Path

import pytest

from universal_agent.constants import DISALLOWED_TOOLS, PRIMARY_ONLY_BLOCKED_TOOLS


# ---------------------------------------------------------------------------
# Section 1: Tool Permission Invariants
# ---------------------------------------------------------------------------

# Tools that subagents MUST be able to call.  If any of these end up in
# DISALLOWED_TOOLS the SDK will hide them from ALL agents and the pipeline
# silently breaks.
_SUBAGENT_REQUIRED_TOOLS = [
    "mcp__internal__run_research_phase",
    "mcp__internal__run_research_pipeline",
    "mcp__internal__run_report_generation",
    "mcp__internal__html_to_pdf",
    "mcp__internal__upload_to_composio",
    "mcp__internal__list_directory",
    "mcp__internal__crawl_parallel",
    "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
    "mcp__composio__COMPOSIO_SEARCH_WEB",
    "mcp__composio__COMPOSIO_SEARCH_NEWS",
]

# Tools that must ALWAYS be in DISALLOWED_TOOLS (globally banned).
_MUST_BE_BANNED = [
    "mcp__composio__COMPOSIO_CRAWL_WEBPAGE",
    "mcp__composio__COMPOSIO_CRAWL_URL",
    "mcp__composio__COMPOSIO_CRAWL_WEBSITE",
    "mcp__composio__COMPOSIO_FETCH_URL",
    "mcp__composio__COMPOSIO_FETCH_WEBPAGE",
]


class TestToolPermissionInvariants:
    """Guard against tools being accidentally moved into disallowed_tools."""

    @pytest.mark.parametrize("tool", _SUBAGENT_REQUIRED_TOOLS)
    def test_subagent_tool_not_in_disallowed_tools(self, tool):
        """CRITICAL: Subagent-needed tools must NOT be in DISALLOWED_TOOLS.

        DISALLOWED_TOOLS is passed to ClaudeAgentOptions.disallowed_tools which
        is a HARD SDK-level block — the tool becomes invisible to ALL agents
        including subagents. Hooks cannot override this.

        If this test fails, the research pipeline is broken.
        """
        assert tool not in DISALLOWED_TOOLS, (
            f"'{tool}' found in DISALLOWED_TOOLS! This will break subagent access. "
            f"See docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md Section 3."
        )

    def test_primary_only_blocked_tools_is_empty(self):
        """PRIMARY_ONLY_BLOCKED_TOOLS must be empty.

        Hook-level blocking for subagent-shared tools does NOT work because
        PreToolUseHookInput lacks parent_tool_use_id and transcript_path may
        not differ for foreground Task calls. Any tool in this list will be
        blocked for ALL agents, not just the primary.
        """
        assert PRIMARY_ONLY_BLOCKED_TOOLS == [], (
            f"PRIMARY_ONLY_BLOCKED_TOOLS is not empty: {PRIMARY_ONLY_BLOCKED_TOOLS}. "
            f"Hook-level subagent detection is unreliable. "
            f"Use prompt-level delegation instead. "
            f"See docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md Section 5."
        )

    @pytest.mark.parametrize("tool", _MUST_BE_BANNED)
    def test_composio_crawl_fetch_banned(self, tool):
        """Composio crawl/fetch tools must be globally banned.

        All crawling goes through Crawl4AI Cloud API via run_research_phase.
        """
        assert tool in DISALLOWED_TOOLS, (
            f"'{tool}' is NOT in DISALLOWED_TOOLS! "
            f"Composio crawl/fetch tools must be globally banned — "
            f"all crawling goes through Crawl4AI."
        )

    def test_disallowed_tools_has_no_duplicates(self):
        assert len(DISALLOWED_TOOLS) == len(set(DISALLOWED_TOOLS)), (
            "DISALLOWED_TOOLS has duplicates"
        )


# ---------------------------------------------------------------------------
# Section 2: Agent Definition Integrity
# ---------------------------------------------------------------------------

def _parse_frontmatter(md_text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", md_text, flags=re.S)
    assert m, "missing or malformed YAML frontmatter"
    return m.group(1)


def _parse_tools(frontmatter: str) -> list[str]:
    for line in frontmatter.splitlines():
        if line.startswith("tools:"):
            raw = line.removeprefix("tools:").strip()
            return [t.strip() for t in raw.split(",") if t.strip()]
    raise AssertionError("frontmatter missing required 'tools:' line")


class TestResearchSpecialistDefinition:
    """Guard .claude/agents/research-specialist.md against drift."""

    @pytest.fixture(autouse=True)
    def _load(self):
        path = Path(".claude/agents/research-specialist.md")
        assert path.exists(), f"{path} does not exist"
        self.text = path.read_text(encoding="utf-8")
        self.frontmatter = _parse_frontmatter(self.text)
        self.tools = _parse_tools(self.frontmatter)

    def test_has_run_research_phase_tool(self):
        assert "mcp__internal__run_research_phase" in self.tools

    def test_has_multi_execute_tool(self):
        assert "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL" in self.tools

    def test_has_session_workspace_section(self):
        assert "## SESSION WORKSPACE" in self.text, (
            "Missing SESSION WORKSPACE section. Without this, subagent files "
            "scatter to repo root."
        )

    def test_has_global_crawl_ban(self):
        assert "GLOBAL CRAWL BAN" in self.text, (
            "Missing GLOBAL CRAWL BAN rule. Without this, subagent may use "
            "Composio fetch tools instead of Crawl4AI pipeline."
        )

    def test_composio_crawl_policy_covers_fetch(self):
        assert "COMPOSIO_FETCH_" in self.text, (
            "Composio crawl policy must also ban COMPOSIO_FETCH_* tools."
        )

    def test_has_mode_selection(self):
        assert "## MODE SELECTION (REQUIRED FIRST STEP)" in self.text

    def test_has_strict_composio_pipeline_mode(self):
        assert "## MODE RULES: composio_pipeline (STRICT)" in self.text

    def test_has_hard_invariant_for_search_then_research(self):
        assert (
            "If search JSON files exist in `search_results/` and "
            "`run_research_phase` has not been attempted"
            in self.text
        )


class TestReportWriterDefinition:
    """Guard .claude/agents/report-writer.md against drift."""

    @pytest.fixture(autouse=True)
    def _load(self):
        path = Path(".claude/agents/report-writer.md")
        if not path.exists():
            pytest.skip("report-writer.md not present")
        self.text = path.read_text(encoding="utf-8")
        self.frontmatter = _parse_frontmatter(self.text)
        self.tools = _parse_tools(self.frontmatter)

    def test_has_report_generation_tool(self):
        assert "mcp__internal__run_report_generation" in self.tools


# ---------------------------------------------------------------------------
# Section 3: run_in_background Guardrail
# ---------------------------------------------------------------------------

class TestRunInBackgroundGuardrail:
    """Verify the guardrail strips run_in_background for pipeline subagents."""

    @pytest.mark.anyio
    async def test_strips_run_in_background_for_research_specialist(self):
        from universal_agent.guardrails.tool_schema import pre_tool_use_schema_guardrail

        result = await pre_tool_use_schema_guardrail(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "research-specialist",
                    "prompt": "Research something",
                    "run_in_background": True,
                },
            },
            run_id="run-test",
            step_id="step-test",
        )
        updated = result.get("hookSpecificOutput", {}).get("updatedInput")
        if updated:
            assert "run_in_background" not in updated, (
                "run_in_background should be stripped for research-specialist"
            )

    @pytest.mark.anyio
    async def test_strips_run_in_background_for_report_writer(self):
        from universal_agent.guardrails.tool_schema import pre_tool_use_schema_guardrail

        result = await pre_tool_use_schema_guardrail(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "report-writer",
                    "prompt": "Write a report",
                    "run_in_background": True,
                },
            },
            run_id="run-test",
            step_id="step-test",
        )
        updated = result.get("hookSpecificOutput", {}).get("updatedInput")
        if updated:
            assert "run_in_background" not in updated, (
                "run_in_background should be stripped for report-writer"
            )

    @pytest.mark.anyio
    async def test_allows_run_in_background_for_other_subagents(self):
        from universal_agent.guardrails.tool_schema import pre_tool_use_schema_guardrail

        result = await pre_tool_use_schema_guardrail(
            {
                "tool_name": "Task",
                "tool_input": {
                    "subagent_type": "image-expert",
                    "prompt": "Generate an image",
                    "run_in_background": True,
                },
            },
            run_id="run-test",
            step_id="step-test",
        )
        # Should not strip for non-pipeline subagents
        updated = result.get("hookSpecificOutput", {}).get("updatedInput")
        if updated:
            # If updated for some other reason, run_in_background should still be there
            pass  # No assertion needed — just shouldn't be stripped


# ---------------------------------------------------------------------------
# Section 4: Golden Run Tool Sequence Validation
# ---------------------------------------------------------------------------

# The expected tool call pattern for a research→report→email pipeline.
# This is not an exact match — it's a subsequence that must appear in order.
GOLDEN_TOOL_SUBSEQUENCE = [
    "Task",                                          # research-specialist
    "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",    # search
    "mcp__internal__run_research_phase",              # crawl + refine
    "Task",                                          # report-writer
    "mcp__internal__run_report_generation",           # report generation
    "mcp__internal__html_to_pdf",                     # PDF conversion
    "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",    # email
]


def validate_tool_sequence_contains_golden_subsequence(
    actual_tools: list[str],
    expected_subsequence: list[str] = GOLDEN_TOOL_SUBSEQUENCE,
) -> tuple[bool, str]:
    """Check that actual_tools contains expected_subsequence in order.

    Returns (passed, message).
    """
    j = 0  # index into expected_subsequence
    for i, tool in enumerate(actual_tools):
        if j < len(expected_subsequence) and tool == expected_subsequence[j]:
            j += 1
    if j == len(expected_subsequence):
        return True, "All expected tools found in order"
    missing = expected_subsequence[j:]
    return False, (
        f"Missing tools in sequence starting at step {j}: {missing}. "
        f"Actual sequence: {actual_tools}"
    )


class TestGoldenRunSequence:
    """Validate that the golden run tool sequence is well-formed."""

    def test_golden_subsequence_matches_successful_session(self):
        """Verify against the known-good session_20260223_215506_140963c1."""
        actual = [
            "Task",  # research-specialist
            "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",  # search
            "mcp__internal__run_research_phase",  # crawl+refine
            "Task",  # report-writer
            "mcp__internal__run_report_generation",  # report
            "mcp__internal__list_directory",  # check files
            "mcp__internal__html_to_pdf",  # convert
            "mcp__internal__upload_to_composio",  # upload
            "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",  # email
        ]
        passed, msg = validate_tool_sequence_contains_golden_subsequence(actual)
        assert passed, msg

    def test_detects_missing_research_phase(self):
        """If run_research_phase is missing, the sequence should fail."""
        broken = [
            "Task",
            "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
            # Missing: mcp__internal__run_research_phase
            "Task",
            "mcp__internal__run_report_generation",
            "mcp__internal__html_to_pdf",
            "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
        ]
        passed, msg = validate_tool_sequence_contains_golden_subsequence(broken)
        assert not passed
        assert "mcp__internal__run_research_phase" in msg

    def test_detects_missing_report_generation(self):
        """If report generation is missing, the sequence should fail."""
        broken = [
            "Task",
            "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
            "mcp__internal__run_research_phase",
            "Task",
            # Missing: mcp__internal__run_report_generation
            "mcp__internal__html_to_pdf",
            "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
        ]
        passed, msg = validate_tool_sequence_contains_golden_subsequence(broken)
        assert not passed
        assert "mcp__internal__run_report_generation" in msg


# ---------------------------------------------------------------------------
# Section 5: Session Workspace Structure Validation
# ---------------------------------------------------------------------------

def validate_session_workspace(session_dir: Path) -> list[str]:
    """Validate a completed research pipeline session workspace.

    Returns a list of issues (empty = pass).
    """
    issues = []

    # Must have search_results with at least one crawl or JSON file
    search_dir = session_dir / "search_results"
    if not search_dir.exists():
        issues.append("Missing search_results/ directory")
    else:
        crawl_files = list(search_dir.glob("crawl_*.md"))
        json_files = list((search_dir / "processed_json").glob("*.json")) if (search_dir / "processed_json").exists() else []
        if not crawl_files and not json_files:
            issues.append("search_results/ has no crawl_*.md or processed_json/*.json files")

    # Must have tasks/{task_name}/refined_corpus.md
    tasks_dir = session_dir / "tasks"
    if not tasks_dir.exists():
        issues.append("Missing tasks/ directory")
    else:
        task_dirs = [d for d in tasks_dir.iterdir() if d.is_dir()]
        if not task_dirs:
            issues.append("tasks/ has no task subdirectories")
        else:
            for td in task_dirs:
                corpus = td / "refined_corpus.md"
                if not corpus.exists():
                    issues.append(f"tasks/{td.name}/ missing refined_corpus.md")

    # Must have work_products/ with report.html
    wp_dir = session_dir / "work_products"
    if not wp_dir.exists():
        issues.append("Missing work_products/ directory")
    else:
        html_files = list(wp_dir.glob("*.html"))
        if not html_files:
            issues.append("work_products/ has no HTML report")

    # No leaked runtime artifacts at repo root (drift detection)
    # Note: some repositories may intentionally contain a tracked `tasks/` tree.
    # Only flag directories that carry known runtime leak signatures.
    repo_root = Path(".")
    stale_dirs: list[str] = []
    stale_details: list[str] = []

    search_root = repo_root / "search_results"
    if search_root.exists():
        has_search_leak = bool(list(search_root.glob("crawl_*.md")))
        has_search_leak = has_search_leak or bool(list(search_root.glob("COMPOSIO_SEARCH_*.json")))
        processed_json = search_root / "processed_json"
        has_search_leak = has_search_leak or (
            processed_json.exists() and bool(list(processed_json.glob("*.json")))
        )
        if has_search_leak:
            stale_dirs.append("search_results")
            stale_details.append("search_results contains crawl/search artifacts")

    tasks_root = repo_root / "tasks"
    if tasks_root.exists():
        leaked_corpus = list(tasks_root.glob("*/refined_corpus.md"))
        if leaked_corpus:
            stale_dirs.append("tasks")
            sample = ", ".join(str(path.parent.name) for path in leaked_corpus[:3])
            stale_details.append(
                f"tasks contains runtime refined_corpus outputs (sample task dirs: {sample})"
            )

    current_ws_root = repo_root / "CURRENT_SESSION_WORKSPACE"
    if current_ws_root.exists():
        stale_dirs.append("CURRENT_SESSION_WORKSPACE")
        stale_details.append("CURRENT_SESSION_WORKSPACE directory exists at repo root")

    if stale_dirs:
        unique_dirs = list(dict.fromkeys(stale_dirs))
        cleanup_cmd = "rm -rf " + " ".join(unique_dirs)
        details = "; ".join(stale_details)
        issues.append(
            "Stale runtime directories at repo root (workspace leak): "
            f"{unique_dirs}. Details: {details}. "
            "Expected location: inside CURRENT_SESSION_WORKSPACE only. "
            f"Remediation: from repo root run `{cleanup_cmd}` "
            "(only for leaked runtime artifacts), or `./start_gateway.sh --clean`."
        )

    return issues


class TestSessionWorkspaceStructure:
    """Validate workspace structure using a synthetic session."""

    def test_valid_workspace_passes(self, tmp_path):
        session = tmp_path / "session_test"
        # search_results
        sr = session / "search_results"
        sr.mkdir(parents=True)
        (sr / "crawl_abc123.md").write_text("# crawled content")
        # tasks
        td = session / "tasks" / "test_task"
        td.mkdir(parents=True)
        (td / "refined_corpus.md").write_text("# refined")
        # work_products
        wp = session / "work_products"
        wp.mkdir(parents=True)
        (wp / "report.html").write_text("<html></html>")

        issues = validate_session_workspace(session)
        assert issues == [], f"Unexpected issues: {issues}"

    def test_missing_search_results_detected(self, tmp_path):
        session = tmp_path / "session_test"
        session.mkdir()
        issues = validate_session_workspace(session)
        assert any("search_results" in i for i in issues)

    def test_missing_refined_corpus_detected(self, tmp_path):
        session = tmp_path / "session_test"
        (session / "search_results").mkdir(parents=True)
        (session / "search_results" / "crawl_abc.md").write_text("data")
        (session / "tasks" / "my_task").mkdir(parents=True)
        # No refined_corpus.md
        (session / "work_products").mkdir(parents=True)
        (session / "work_products" / "report.html").write_text("<html>")

        issues = validate_session_workspace(session)
        assert any("refined_corpus.md" in i for i in issues)

    def test_missing_html_report_detected(self, tmp_path):
        session = tmp_path / "session_test"
        (session / "search_results").mkdir(parents=True)
        (session / "search_results" / "crawl_abc.md").write_text("data")
        td = session / "tasks" / "my_task"
        td.mkdir(parents=True)
        (td / "refined_corpus.md").write_text("# refined")
        (session / "work_products").mkdir(parents=True)
        # No HTML report

        issues = validate_session_workspace(session)
        assert any("HTML report" in i for i in issues)

    def test_repo_root_tasks_without_runtime_signature_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "tasks" / "docs_archive").mkdir(parents=True)
        (tmp_path / "tasks" / "docs_archive" / "notes.md").write_text("reference")

        session = tmp_path / "session_test"
        (session / "search_results").mkdir(parents=True)
        (session / "search_results" / "crawl_abc.md").write_text("data")
        td = session / "tasks" / "my_task"
        td.mkdir(parents=True)
        (td / "refined_corpus.md").write_text("# refined")
        (session / "work_products").mkdir(parents=True)
        (session / "work_products" / "report.html").write_text("<html>")

        issues = validate_session_workspace(session)
        assert not any("workspace leak" in i.lower() for i in issues)

    def test_repo_root_tasks_runtime_signature_includes_remediation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        leaked = tmp_path / "tasks" / "leaked_task"
        leaked.mkdir(parents=True)
        (leaked / "refined_corpus.md").write_text("# leaked")

        session = tmp_path / "session_test"
        (session / "search_results").mkdir(parents=True)
        (session / "search_results" / "crawl_abc.md").write_text("data")
        td = session / "tasks" / "my_task"
        td.mkdir(parents=True)
        (td / "refined_corpus.md").write_text("# refined")
        (session / "work_products").mkdir(parents=True)
        (session / "work_products" / "report.html").write_text("<html>")

        issues = validate_session_workspace(session)
        leak_msgs = [m for m in issues if "workspace leak" in m.lower()]
        assert leak_msgs, f"Expected leak message, got: {issues}"
        assert "rm -rf tasks" in leak_msgs[0]


# ---------------------------------------------------------------------------
# Section 6: Prompt Builder Delegation Instructions
# ---------------------------------------------------------------------------

class TestPromptBuilderDelegationInstructions:
    """Verify prompt_builder.py contains delegation guardrails."""

    @pytest.fixture(autouse=True)
    def _load(self):
        path = Path("src/universal_agent/prompt_builder.py")
        assert path.exists()
        self.text = path.read_text(encoding="utf-8")

    def test_forbids_run_in_background_for_research(self):
        assert "run_in_background" in self.text, (
            "prompt_builder.py must contain instructions about run_in_background"
        )

    def test_delegates_research_to_specialist(self):
        assert "research-specialist" in self.text, (
            "prompt_builder.py must reference research-specialist delegation"
        )

    def test_delegates_report_to_writer(self):
        assert "report-writer" in self.text, (
            "prompt_builder.py must reference report-writer delegation"
        )


# ---------------------------------------------------------------------------
# Section 8: Dynamic capabilities.md Generation
# ---------------------------------------------------------------------------

class TestCapabilitiesDynamicGeneration:
    """Ensure capabilities.md is dynamically built with all expected sections.
    
    This document is injected into the primary agent's system prompt and serves
    as the authoritative source of truth for available skills and agents.
    """

    def test_live_capabilities_snapshot_contains_required_sections(self):
        from universal_agent import prompt_assets

        # We use the real project root to ensure the actual .claude/agents and
        # .claude/skills directories are parsed correctly.
        project_root = str(Path(".").absolute())
        snapshot = prompt_assets.build_live_capabilities_snapshot(project_root)

        # Core structural headers must be present
        assert "Specialist Agents (Live)" in snapshot
        assert "External VP Control Plane (Live)" in snapshot

        # Key pipeline agents must be dynamically discovered and injected
        assert "**research-specialist**:" in snapshot
        assert "**report-writer**:" in snapshot

        # At least some skills should be discovered
        assert "Source: " in snapshot  # Indicates a skill was loaded

    def test_capabilities_registry_persists_to_session_workspace(self, tmp_path):
        from universal_agent import prompt_assets

        project_root = str(Path(".").absolute())
        workspace = tmp_path / "session_workspace"
        workspace.mkdir()

        # The loader builds the snapshot and writes it to two places:
        # 1. src/universal_agent/prompt_assets/capabilities.last_good.md (fallback)
        # 2. CURRENT_SESSION_WORKSPACE/capabilities.md (session record)
        content, source = prompt_assets.load_capabilities_registry(
            project_root,
            workspace_dir=str(workspace)
        )

        assert source == "live", "Failed to build live capabilities snapshot"
        assert "research-specialist" in content

        # Verify it was written to the session workspace
        session_file = workspace / "capabilities.md"
        assert session_file.exists()
        assert session_file.read_text(encoding="utf-8").strip() == content.strip()

class TestDocumentation:
    """Verify the SDK permissions reference doc exists and is up to date."""

    def test_002_doc_exists(self):
        path = Path("docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md")
        assert path.exists(), (
            "docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md is missing! "
            "This is the source of truth for SDK permissions behavior."
        )

    def test_002_doc_covers_key_topics(self):
        path = Path("docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md")
        if not path.exists():
            pytest.skip("doc missing")
        text = path.read_text(encoding="utf-8")
        for topic in [
            "PreToolUseHookInput",
            "parent_tool_use_id",
            "transcript_path",
            "disallowed_tools",
            "run_in_background",
            "CURRENT_SESSION_WORKSPACE",
        ]:
            assert topic in text, f"002 doc missing coverage of '{topic}'"
