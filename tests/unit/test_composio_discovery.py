"""Unit tests for ``utils/composio_discovery.py``.

These pin the pure branching behaviour of the four module functions. The only
external dependency — the Composio client — is dependency-injected as ``Any``,
so every test passes a lightweight stub built from ``types.SimpleNamespace``;
no SDK, network, or DB is touched.

Covered behaviour:
  - get_local_tools: returns the in-process MCP tool slug list
  - discover_connected_toolkits: two ACTIVE-detection paths, defaults injection,
    dedup + sort, the project-policy Twitter exclusion, malformed-item skipping,
    correct API call args, and exception -> [] contract
  - fetch_toolkit_meta: API population, category extraction, the fallback
    description table, and exception -> default meta
  - discover_connected_toolkits_with_meta: composition over the two helpers
"""
from __future__ import annotations

import types

import pytest

from universal_agent.utils.composio_discovery import (
    INPROCESS_MCP_TOOLS,
    discover_connected_toolkits,
    discover_connected_toolkits_with_meta,
    fetch_toolkit_meta,
    get_local_tools,
)

# Defaults that ``discover_connected_toolkits`` always injects (never Twitter).
DEFAULTS = ("codeinterpreter", "composio_search")


# ── stub builders ────────────────────────────────────────────────────────


def _item(slug, *, status=None, connection_status=None, toolkit="set", has_slug=True):
    """Build a fake connected-account item.

    toolkit="set" -> real toolkit namespace; toolkit=None -> falsy toolkit;
    toolkit="missing" -> no toolkit attribute at all.
    """
    item = types.SimpleNamespace()
    if toolkit == "set":
        tk = types.SimpleNamespace()
        if has_slug:
            tk.slug = slug
        item.toolkit = tk
    elif toolkit is None:
        item.toolkit = None
    # toolkit == "missing" -> attribute simply absent
    if status is not None:
        item.status = status
    if connection_status is not None:
        item.connection = types.SimpleNamespace(status=connection_status)
    return item


class _FakeConnectedAccounts:
    def __init__(self, items=None, *, raise_exc=None, drop_items=False):
        self._items = list(items or [])
        self._raise = raise_exc
        self._drop_items = drop_items
        self.last_kwargs = None

    def list(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raise is not None:
            raise self._raise
        if self._drop_items:
            return types.SimpleNamespace()  # no ``items`` attribute
        return types.SimpleNamespace(items=self._items)


class _FakeToolkits:
    def __init__(self, mapping=None, *, raise_exc=None):
        self._mapping = mapping or {}
        self._raise = raise_exc

    def get(self, slug):
        if self._raise is not None:
            raise self._raise
        return self._mapping[slug]


def _client(*, connected_accounts=None, toolkits=None):
    return types.SimpleNamespace(connected_accounts=connected_accounts, toolkits=toolkits)


# ── get_local_tools ──────────────────────────────────────────────────────


class TestGetLocalTools:
    def test_returns_module_constant(self):
        assert get_local_tools() is INPROCESS_MCP_TOOLS

    def test_non_empty_list_of_strings(self):
        tools = get_local_tools()
        assert isinstance(tools, list)
        assert tools
        assert all(isinstance(t, str) for t in tools)


# ── discover_connected_toolkits ──────────────────────────────────────────


class TestDiscoverConnectedToolkits:
    def test_top_level_active_status_included(self):
        accts = _FakeConnectedAccounts([_item("gmail", status="ACTIVE")])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert "gmail" in result

    def test_nested_connection_active_included(self):
        # Top-level status not ACTIVE, but nested connection.status is.
        accts = _FakeConnectedAccounts(
            [_item("slack", status="PENDING", connection_status="ACTIVE")]
        )
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert "slack" in result

    def test_inactive_item_excluded(self):
        accts = _FakeConnectedAccounts([_item("github", status="INACTIVE")])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert "github" not in result
        # defaults still present
        for d in DEFAULTS:
            assert d in result

    def test_defaults_always_present_with_empty_items(self):
        accts = _FakeConnectedAccounts([])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result == sorted(DEFAULTS)

    def test_result_is_sorted(self):
        accts = _FakeConnectedAccounts(
            [_item("slack", status="ACTIVE"), _item("airtable", status="ACTIVE")]
        )
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result == sorted(result)

    def test_duplicate_slugs_deduped(self):
        accts = _FakeConnectedAccounts(
            [_item("slack", status="ACTIVE"), _item("slack", status="ACTIVE")]
        )
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result.count("slack") == 1

    def test_active_slug_equal_to_default_deduped(self):
        accts = _FakeConnectedAccounts([_item("codeinterpreter", status="ACTIVE")])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result.count("codeinterpreter") == 1

    @pytest.mark.parametrize("slug", ["twitter", "Twitter", "TWITTER", " twitter "])
    def test_twitter_excluded_case_insensitive(self, slug):
        accts = _FakeConnectedAccounts([_item(slug, status="ACTIVE")])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert not any(str(s).strip().lower() == "twitter" for s in result)

    def test_exception_returns_empty_list(self):
        accts = _FakeConnectedAccounts(raise_exc=RuntimeError("boom"))
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result == []

    def test_response_without_items_returns_defaults_only(self):
        accts = _FakeConnectedAccounts(drop_items=True)
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result == sorted(DEFAULTS)

    def test_none_toolkit_item_skipped(self):
        accts = _FakeConnectedAccounts([_item("x", status="ACTIVE", toolkit=None)])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result == sorted(DEFAULTS)

    def test_missing_toolkit_attr_skipped(self):
        accts = _FakeConnectedAccounts([_item("x", status="ACTIVE", toolkit="missing")])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result == sorted(DEFAULTS)

    def test_toolkit_without_slug_skipped(self):
        accts = _FakeConnectedAccounts([_item("x", status="ACTIVE", has_slug=False)])
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert result == sorted(DEFAULTS)

    def test_passes_expected_api_call_args(self):
        accts = _FakeConnectedAccounts([])
        discover_connected_toolkits(_client(connected_accounts=accts), "user-42")
        assert accts.last_kwargs == {"user_ids": ["user-42"], "limit": 50}

    def test_mixed_active_and_inactive(self):
        accts = _FakeConnectedAccounts(
            [
                _item("gmail", status="ACTIVE"),
                _item("github", status="INACTIVE"),
                _item("slack", connection_status="ACTIVE"),
            ]
        )
        result = discover_connected_toolkits(_client(connected_accounts=accts), "u1")
        assert "gmail" in result
        assert "slack" in result
        assert "github" not in result


# ── fetch_toolkit_meta ───────────────────────────────────────────────────


class TestFetchToolkitMeta:
    def test_populates_from_api(self):
        tk = types.SimpleNamespace(description="Custom desc", name="Gmail Pro")
        client = _client(toolkits=_FakeToolkits({"gmail": tk}))
        meta = fetch_toolkit_meta(client, "gmail")
        assert meta["slug"] == "gmail"
        assert meta["description"] == "Custom desc"
        assert meta["name"] == "Gmail Pro"

    def test_extracts_categories(self):
        cats = types.SimpleNamespace(
            categories=[types.SimpleNamespace(name="Email"), types.SimpleNamespace(name="Comms")]
        )
        tk = types.SimpleNamespace(description="d", name="n", meta=cats)
        client = _client(toolkits=_FakeToolkits({"gmail": tk}))
        meta = fetch_toolkit_meta(client, "gmail")
        assert meta["categories"] == ["Email", "Comms"]

    def test_exception_falls_back_to_default_meta(self):
        client = _client(toolkits=_FakeToolkits(raise_exc=RuntimeError("nope")))
        meta = fetch_toolkit_meta(client, "github")
        # API failed, but the known-slug fallback table still fills description.
        assert meta["slug"] == "github"
        assert meta["name"] == "Github"  # slug.title()
        assert meta["description"] == "Code hosting and collaboration platform."

    def test_unknown_slug_empty_description(self):
        client = _client(toolkits=_FakeToolkits(raise_exc=RuntimeError("nope")))
        meta = fetch_toolkit_meta(client, "totallyunknownslug")
        assert meta["description"] == ""
        assert meta["name"] == "Totallyunknownslug"  # slug.title()

    def test_empty_api_description_uses_fallback_table(self):
        # API supplies a name but empty description -> fallback fills description,
        # API name is preserved.
        tk = types.SimpleNamespace(description="", name="My Slack")
        client = _client(toolkits=_FakeToolkits({"slack": tk}))
        meta = fetch_toolkit_meta(client, "slack")
        assert meta["name"] == "My Slack"
        assert meta["description"] == "Team messaging and collaboration in Slack workspaces."

    def test_api_description_overrides_fallback(self):
        tk = types.SimpleNamespace(description="Bespoke", name="GH")
        client = _client(toolkits=_FakeToolkits({"github": tk}))
        meta = fetch_toolkit_meta(client, "github")
        assert meta["description"] == "Bespoke"

    @pytest.mark.parametrize(
        "slug,needle",
        [
            ("codeinterpreter", "sandboxed"),
            ("composio_search", "Search engine"),
            ("sqltool", "SQL"),
            ("filetool", "files"),
            ("googlecalendar", "Calendar"),
        ],
    )
    def test_known_slug_fallback_descriptions(self, slug, needle):
        client = _client(toolkits=_FakeToolkits(raise_exc=RuntimeError("nope")))
        meta = fetch_toolkit_meta(client, slug)
        assert needle in meta["description"]


# ── discover_connected_toolkits_with_meta ────────────────────────────────


class TestDiscoverConnectedToolkitsWithMeta:
    def test_composition_returns_meta_per_slug(self):
        accts = _FakeConnectedAccounts([_item("gmail", status="ACTIVE")])
        tk = types.SimpleNamespace(description="Email service", name="Gmail")
        # Defaults (codeinterpreter, composio_search) also resolved via fallback.
        toolkits = _FakeToolkits(raise_exc=None, mapping={})
        # toolkits.get must succeed for every discovered slug; use a mapping that
        # returns an empty namespace so fallback descriptions kick in.
        toolkits = _FakeToolkits(
            {
                "gmail": tk,
                "codeinterpreter": types.SimpleNamespace(),
                "composio_search": types.SimpleNamespace(),
            }
        )
        client = _client(connected_accounts=accts, toolkits=toolkits)
        results = discover_connected_toolkits_with_meta(client, "u1")
        slugs = {r["slug"] for r in results}
        assert "gmail" in slugs
        assert {"codeinterpreter", "composio_search"} <= slugs
        for r in results:
            assert {"slug", "name", "description"} <= set(r)

    def test_empty_discovery_returns_empty_list(self):
        accts = _FakeConnectedAccounts(raise_exc=RuntimeError("boom"))
        client = _client(connected_accounts=accts, toolkits=_FakeToolkits())
        assert discover_connected_toolkits_with_meta(client, "u1") == []
