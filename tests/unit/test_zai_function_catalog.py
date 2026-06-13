"""ZAI function/stage catalog — the pre-built, zero-runtime-LLM stage lookup that
annotates the token panel. Covers load/lookup, source-hash staleness, coverage,
and the `build_token_usage` catalog join.
"""

from __future__ import annotations

import json
import time

from universal_agent.services import zai_function_catalog as cat, zai_status


def test_committed_catalog_loads_and_hashes_resolve():
    # The shipped catalog must parse and every stage entry's source_sha must
    # still resolve to a function in the tree (else the key rotted).
    catalog = cat.load_catalog()
    assert catalog.get("version", 0) >= 1
    entries = catalog["entries"]
    assert len(entries) >= 10
    unresolved = [k for k in entries if "::" in k and cat.function_source_hash(k) is None]
    assert not unresolved, f"catalog keys whose function no longer resolves: {unresolved}"


def test_committed_catalog_not_stale():
    # Freshly generated catalog: stored source_sha == current hash for every
    # stage. (This guards that the committed JSON was regenerated after edits.)
    annotated = cat.annotate_stale()
    stale = [k for k, e in annotated.items()
             if "::" in k and isinstance(e, dict) and e.get("stale")]
    assert not stale, f"stale catalog entries (regenerate the JSON): {stale}"


def test_lookup_exact_and_file_level_fallback(tmp_path, monkeypatch):
    catalog = {
        "version": 1,
        "entries": {
            "universal_agent/services/foo.py::bar": {"label": "Bar"},
            "universal_agent/services/baz.py": {"label": "Baz (file-level)"},
        },
    }
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(catalog))
    monkeypatch.setenv("UA_ZAI_FUNCTION_CATALOG_PATH", str(p))

    assert cat.lookup("universal_agent/services/foo.py::bar")["label"] == "Bar"
    # file-level fallback when the exact stage isn't described
    assert cat.lookup("universal_agent/services/baz.py::anything")["label"] == "Baz (file-level)"
    assert cat.lookup("universal_agent/services/unknown.py::x") is None


def test_source_hash_detects_drift(tmp_path, monkeypatch):
    # A real stage key resolves to a non-None hash; a tampered stored sha is
    # flagged stale.
    key = "universal_agent/services/zai_status.py::analyze_token_usage"
    real = cat.function_source_hash(key)
    assert real is not None

    catalog = {"version": 1, "entries": {
        key: {"label": "x", "source_sha": "deadbeefdeadbeef"},  # wrong on purpose
    }}
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(catalog))
    monkeypatch.setenv("UA_ZAI_FUNCTION_CATALOG_PATH", str(p))
    annotated = cat.annotate_stale()
    assert annotated[key]["stale"] is True

    # matching sha → not stale
    catalog["entries"][key]["source_sha"] = real
    p.write_text(json.dumps(catalog))
    annotated = cat.annotate_stale()
    assert annotated[key]["stale"] is False


def test_coverage_reports_undescribed():
    catalog = {"version": 1, "entries": {"universal_agent/a.py::known": {"label": "K"}}}
    cov = cat.coverage(
        ["universal_agent/a.py::known", "universal_agent/b.py::mystery",
         "universal_agent/b.py::mystery"],
        catalog,
    )
    assert cov["described_count"] == 1
    assert cov["undescribed_count"] == 1  # de-duped
    assert cov["undescribed"] == ["universal_agent/b.py::mystery"]


def test_coverage_ignores_non_stage_keys():
    # Legacy file-level events (no ::) and <string> exec frames are NOT
    # describable stages — they must not inflate the undescribed count.
    catalog = {"version": 1, "entries": {}}
    cov = cat.coverage(
        [
            "universal_agent/services/csi_watchlist.py",  # legacy file-level (no ::)
            "<string>",                                   # exec/REPL frame
            "<string>::run",                              # exec frame w/ fn — not a .py source
            "universal_agent/b.py::real_stage",           # the only real stage
        ],
        catalog,
    )
    assert cov["undescribed"] == ["universal_agent/b.py::real_stage"]
    assert cov["undescribed_count"] == 1
    assert cov["described_count"] == 0


def test_build_token_usage_joins_catalog(tmp_path, monkeypatch):
    now = time.time()
    csi = "universal_agent/api/routers/csi_watchlist.py"
    events = [
        {"ts": now - 20, "category": "ok", "model": "glm-5-turbo", "caller": csi,
         "caller_fn": f"{csi}::_classify_channel_llm",
         "input_tokens": 500, "output_tokens": 15},
        {"ts": now - 10, "category": "ok", "model": "glm-4.5-air",
         "caller": "universal_agent/services/mystery.py",
         "caller_fn": "universal_agent/services/mystery.py::do_thing",
         "input_tokens": 100, "output_tokens": 10},
    ]
    path = tmp_path / "ev.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", str(path))

    rep = zai_status.build_token_usage(3600)
    procs = {p["caller"]: p for p in rep["processes"]}
    # the described stage carries its catalog entry
    csi_stage = procs[csi]["stages"][0]
    assert csi_stage["catalog"]["role"] == "classifier"
    assert csi_stage["catalog"]["tier_verdict"] == "appropriate"
    # the undescribed stage is null + counted in coverage
    assert procs["universal_agent/services/mystery.py"]["stages"][0]["catalog"] is None
    assert "universal_agent/services/mystery.py::do_thing" in rep["catalog"]["coverage"]["undescribed"]
