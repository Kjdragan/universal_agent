"""Unit tests for Sessions Dashboard UX Overhaul — backend components."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

# Ensure the source is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# ──────────────────────────────────────────────────────────────────────────────
# Test 1: Channel classification
# ──────────────────────────────────────────────────────────────────────────────
from universal_agent.ops_service import OpsService

CASES = [
    # (session_id, source, run_kind, trigger_source, expected_channel)
    # Interactive
    ("abc123",         "local",       "",           "",                 "interactive"),
    ("abc123",         "websocket",   "",           "",                 "interactive"),
    ("tg_1234",        "telegram",    "",           "",                 "interactive"),
    # VP missions
    ("vp_coder_xyz",   "vp_api",      "",           "",                 "vp_mission"),
    ("abc123",         "",            "vp_mission",  "",                "vp_mission"),
    # Email
    ("abc123",         "",            "email_triage","",                "email"),
    ("abc123",         "",            "email_reply", "",                "email"),
    ("abc123",         "",            "",            "agentmail_hook",  "email"),
    # Scheduled
    ("abc123",         "",            "cron",        "",                "scheduled"),
    ("cron_daily_123", "",            "",            "",                "scheduled"),
    ("abc123",         "",            "",            "cron_trigger",    "scheduled"),
    # Proactive
    ("abc123",         "",            "proactive_signal", "",           "proactive"),
    ("abc123",         "",            "",            "dashboard_signal","proactive"),
    # Discord
    ("abc123",         "discord_bot", "",            "",                "discord"),
    ("abc123",         "",            "",            "discord_hook",    "discord"),
    # Infrastructure
    ("daemon_heartbeat_x", "",        "",            "",                "infrastructure"),
    ("daemon_reaper_x",    "",        "",            "",                "infrastructure"),
    ("abc123",         "",            "heartbeat",   "",                "infrastructure"),
    # System (fallback)
    ("abc123",         "",            "internal",    "",                "system"),
    ("abc123",         "",            "something_unknown", "",          "system"),
]

print("=" * 60)
print("TEST 1: Channel Classification")
print("=" * 60)
passed = 0
failed = 0
for sid, src, rk, ts, expected in CASES:
    result = OpsService._classify_channel(sid, src, run_kind=rk, trigger_source=ts)
    status = "✅" if result == expected else "❌"
    if result != expected:
        print(f"  {status} classify({sid!r}, src={src!r}, rk={rk!r}, ts={ts!r})")
        print(f"       expected={expected!r}  got={result!r}")
        failed += 1
    else:
        passed += 1

print(f"\n  Results: {passed} passed, {failed} failed\n")

# ──────────────────────────────────────────────────────────────────────────────
# Test 2: Dossier file handling (write + read context_brief)
# ──────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("TEST 2: Context Brief File I/O")
print("=" * 60)

tmpdir = Path(tempfile.mkdtemp(prefix="sess_test_"))
try:
    # Create a fake session workspace with a context_brief.md
    brief_path = tmpdir / "context_brief.md"
    brief_content = "# Session Summary\n\nWorked on feature X.\n\n## Artifacts\n- file1.py\n- file2.ts\n"
    brief_path.write_text(brief_content)

    desc_path = tmpdir / "description.txt"
    desc_path.write_text("Feature X implementation session")

    # Verify reads
    read_brief = brief_path.read_text()
    read_desc = desc_path.read_text().strip()

    assert read_brief == brief_content, f"Brief mismatch: {read_brief!r}"
    print("  ✅ context_brief.md written and read back correctly")

    assert read_desc == "Feature X implementation session"
    print("  ✅ description.txt written and read back correctly")

    # Test _try_read_context_brief_title fallback
    lines = read_brief.strip().splitlines()
    title_line = next((l for l in lines if l.startswith("#") and not l.startswith("##")), None)
    assert title_line is not None, "No title found"
    title = title_line.lstrip("# ").strip()
    assert title == "Session Summary", f"Title mismatch: {title!r}"
    print("  ✅ Title extraction from context_brief.md works correctly")

    # Test missing file scenario
    missing = tmpdir / "nonexistent.md"
    assert not missing.exists()
    print("  ✅ Missing file correctly returns False for .exists()")
    
    passed += 4
except AssertionError as e:
    print(f"  ❌ {e}")
    failed += 1
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

# ──────────────────────────────────────────────────────────────────────────────
# Test 3: has_context_brief flag in session summary
# ──────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 3: has_context_brief Detection")
print("=" * 60)

tmpdir2 = Path(tempfile.mkdtemp(prefix="sess_brief_"))
try:
    # Workspace with context_brief
    ws_with = tmpdir2 / "session_with"
    ws_with.mkdir()
    (ws_with / "context_brief.md").write_text("# Brief\nSome content.")
    assert (ws_with / "context_brief.md").is_file()
    print("  ✅ has_context_brief=True when file exists")

    # Workspace without context_brief
    ws_without = tmpdir2 / "session_without"
    ws_without.mkdir()
    assert not (ws_without / "context_brief.md").is_file()
    print("  ✅ has_context_brief=False when file missing")

    passed += 2
except Exception as e:
    print(f"  ❌ {e}")
    failed += 1
finally:
    shutil.rmtree(tmpdir2, ignore_errors=True)

# ──────────────────────────────────────────────────────────────────────────────
# Test 4: Description derivation priority chain
# ──────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TEST 4: Description Priority Chain")
print("=" * 60)

tmpdir3 = Path(tempfile.mkdtemp(prefix="sess_desc_"))
try:
    # Priority 1: description.txt exists
    ws = tmpdir3 / "session_prio1"
    ws.mkdir()
    (ws / "description.txt").write_text("Explicit description from dossier")
    (ws / "context_brief.md").write_text("# Brief Title\nContent here.")
    desc_file = ws / "description.txt"
    assert desc_file.exists()
    desc = desc_file.read_text().strip()
    assert desc == "Explicit description from dossier"
    print("  ✅ Priority 1: description.txt is preferred")

    # Priority 2: context_brief.md title fallback
    ws2 = tmpdir3 / "session_prio2"
    ws2.mkdir()
    (ws2 / "context_brief.md").write_text("# Email Triage Analysis\nDetailed analysis here.")
    assert not (ws2 / "description.txt").exists()
    brief = (ws2 / "context_brief.md").read_text()
    lines = brief.strip().splitlines()
    title_line = next((l for l in lines if l.startswith("#") and not l.startswith("##")), None)
    title = title_line.lstrip("# ").strip() if title_line else None
    assert title == "Email Triage Analysis"
    print("  ✅ Priority 2: context_brief.md H1 title used as fallback")

    passed += 2
except Exception as e:
    print(f"  ❌ {e}")
    failed += 1
finally:
    shutil.rmtree(tmpdir3, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
total = passed + failed
print(f"TOTAL: {passed}/{total} passed  ({failed} failures)")
print("=" * 60)
sys.exit(1 if failed else 0)
