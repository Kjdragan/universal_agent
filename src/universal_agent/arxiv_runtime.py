"""ArXiv MCP server runtime wiring.

Registers the third-party `arxiv-mcp-server` (https://pypi.org/project/arxiv-mcp-server/)
as a feature-gated stdio MCP server so agents can call
``mcp__arxiv-mcp-server__{search_papers,download_paper,read_paper,list_papers}``
directly.

Why this exists
---------------
The ``paper-to-podcast-tf`` skill (and the ``paper_to_podcast_daily`` cron that
invokes it) was *written against* these arXiv MCP tools but they were never
wired into the gateway's MCP surface — only ``notebooklm-mcp`` was (see
``notebooklm_runtime.build_notebooklm_mcp_server_config``). With no arXiv MCP
tools available, the in-process cron agent was forced to improvise with the raw
``arxiv`` Python library + ``curl``, which repeatedly hit arXiv's HTTP 429 rate
limit and failed the nightly run.

The ``arxiv-mcp-server`` package **enforces arXiv's 3-second rate limit
automatically** and backs off on 429, which is precisely the missing capability.

Enablement mirrors NotebookLM exactly: gated on ``UA_ENABLE_ARXIV_MCP`` (default
off), turned on durably in production via the Infisical ``UA_ENABLE_ARXIV_MCP``
secret which ``initialize_runtime_secrets()`` loads into ``os.environ`` at
process start.

Launch path
-----------
The server ships as a Python package run via uv:

    uv tool run arxiv-mcp-server --storage-path <dir>

We resolve ``uv`` to an absolute path because the systemd unit's PATH typically
omits ``/usr/local/bin`` (where uv lives on the VPS). Pre-install with
``uv tool install arxiv-mcp-server`` so ``uv tool run`` resolves the cached tool
instantly and offline rather than downloading on every cold gateway start.

Storage path contract
---------------------
``canonical_arxiv_storage_path()`` resolves the ONE directory the
arxiv-mcp-server writes to and the paper_to_podcast pipeline reads from. We
ALWAYS pass ``--storage-path`` explicitly so the write path is deterministic -
never relying on the server's implicit home-directory default, which was a
contributing factor in the 2026-06-22 silent no-op. The server writes EVERY
paper - HTML or PDF source - as ``<arxiv_id>.md`` (PDFs are converted to
markdown and the PDF is deleted; see
``arxiv_mcp_server/tools/download.py::get_paper_path`` in v0.5.0), so the
pipeline cache check must look for ``.md`` files, never ``.pdf``.
``resolve_cached_paper_path`` / ``is_paper_cached`` encode that contract.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
from typing import Any

from universal_agent.utils.env_utils import env_flag_3state as _env_flag

logger = logging.getLogger(__name__)

# Canonical default location for the arxiv-mcp-server paper cache. Defaults to
# the server's own historical default (~/.arxiv-mcp-server/papers) so the
# papers already accumulated in production remain reachable without a
# migration. Override via the UA_ARXIV_MCP_STORAGE_PATH env var (set in
# Infisical for production) to relocate to a UA-data directory if desired.
_DEFAULT_ARXIV_STORAGE_PATH = str(Path.home() / ".arxiv-mcp-server" / "papers")


def canonical_arxiv_storage_path() -> Path:
    """Resolve the canonical arxiv-mcp-server storage directory.

    This is the ONE path that the arxiv-mcp-server writes downloaded papers to
    (via --storage-path in build_arxiv_mcp_server_config) AND that the
    paper_to_podcast pipeline reads from to check is-this-paper-cached.
    Centralising the resolver eliminates the read/write-path mismatch class of
    bugs.

    Priority:
      1. UA_ARXIV_MCP_STORAGE_PATH env var (set in Infisical for prod).
      2. _DEFAULT_ARXIV_STORAGE_PATH (the server historical home default, so
         existing cached papers remain reachable without a migration).

    The directory is created on first access (mkdir parents=True) so the server
    never falls back to a different default if the path does not yet exist.

    Returns the resolved absolute path. Pure aside from directory creation.
    """
    raw = str(os.getenv("UA_ARXIV_MCP_STORAGE_PATH") or "").strip()
    path_str = raw if raw else _DEFAULT_ARXIV_STORAGE_PATH
    path = Path(path_str).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "canonical_arxiv_storage_path: could not create %s: %s", path, exc,
        )
    return path


def resolve_cached_paper_path(paper_id: str) -> Path:
    """Return the absolute path the arxiv-mcp-server stores a paper at.

    The server (v0.5.0 tools/download.py::get_paper_path) writes EVERY paper -
    HTML-source or PDF-source - as <paper_id>.md. PDFs are converted to
    markdown and the intermediate .pdf is deleted. So the cache check MUST look
    for <paper_id>.md, never <paper_id>.pdf.

    Pure - does not check existence. Callers do .exists() / .is_file() to test
    whether the paper is cached.
    """
    clean_id = paper_id.strip()
    if clean_id.lower().startswith("arxiv:"):
        clean_id = clean_id[len("arxiv:"):]
    # Collapse any path separators so an attacker-controlled id cannot escape
    # the storage directory.
    clean_id = clean_id.replace("/", "_").replace("\\", "_")
    return canonical_arxiv_storage_path() / f"{clean_id}.md"


def is_paper_cached(paper_id: str) -> bool:
    """Return True iff the arxiv-mcp-server has a cached file for paper_id.

    Handles both HTML-source and PDF-source papers: the server stores both as
    <paper_id>.md (see arxiv_mcp_server/tools/download.py::get_paper_path - PDFs
    are converted to markdown and the intermediate PDF is deleted).
    """
    return resolve_cached_paper_path(paper_id).is_file()


def arxiv_mcp_enabled() -> bool:
    """Feature flag for the arXiv MCP server (default off; on in prod)."""
    return _env_flag("UA_ENABLE_ARXIV_MCP", default=False)


def arxiv_mcp_uv_command() -> str:
    """Resolve the ``uv`` launcher used to run the arXiv MCP server.

    Honours ``UA_ARXIV_MCP_UV_COMMAND`` override; otherwise resolves ``uv`` on
    PATH and finally falls back to the canonical VPS install location. We avoid
    a bare ``"uv"`` so the server still launches when the gateway's (systemd)
    PATH does not include ``/usr/local/bin``.
    """
    override = str(os.getenv("UA_ARXIV_MCP_UV_COMMAND") or "").strip()
    if override:
        return override
    resolved = shutil.which("uv")
    if resolved:
        return resolved
    for candidate in ("/usr/local/bin/uv", "/home/ua/.local/bin/uv"):
        if os.path.exists(candidate):
            return candidate
    return "uv"


def build_arxiv_mcp_server_config() -> dict[str, Any] | None:
    """Return the stdio MCP server config for arXiv, or ``None`` when disabled.

    Shape mirrors ``build_notebooklm_mcp_server_config`` so registration in
    ``agent_setup``/``main`` is symmetric.

    ALWAYS passes ``--storage-path`` pointing at
    ``canonical_arxiv_storage_path()`` so the server write path is
    deterministic and matches the pipeline read path. The 2026-06-22 silent
    no-op traced to the server using its implicit home-directory default
    while the pipeline cache check looked elsewhere for a ``.pdf`` - passing
    the path explicitly and reading it back via ``is_paper_cached``
    eliminates that mismatch class.
    """
    if not arxiv_mcp_enabled():
        return None

    command = arxiv_mcp_uv_command()
    args = ["tool", "run", "arxiv-mcp-server"]

    # Always pass --storage-path explicitly. canonical_arxiv_storage_path()
    # honours the UA_ARXIV_MCP_STORAGE_PATH override (set in Infisical for
    # production) and falls back to the server historical default
    # (~/.arxiv-mcp-server/papers). Migrated from optional to unconditional
    # for path-determinism (2026-06-22 RCA).
    storage_path = canonical_arxiv_storage_path()
    args += ["--storage-path", str(storage_path)]

    # Pass through the small set of tuning vars the server honours, if present.
    env_payload: dict[str, str] = {}
    for key in ("MAX_RESULTS", "REQUEST_TIMEOUT"):
        value = os.getenv(key)
        if value is not None and str(value).strip() != "":
            env_payload[key] = str(value)

    return {
        "type": "stdio",
        "command": command,
        "args": args,
        "env": env_payload,
    }
