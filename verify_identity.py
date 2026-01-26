import os
import sys
from universal_agent.identity import resolve_user_id

def test_resolution():
    print("--- Test 1: No Env Vars ---")
    if "COMPOSIO_USER_ID" in os.environ: del os.environ["COMPOSIO_USER_ID"]
    if "DEFAULT_USER_ID" in os.environ: del os.environ["DEFAULT_USER_ID"]
    print(f"Result: {resolve_user_id()}")
    assert resolve_user_id() == "user_universal"

    print("\n--- Test 2: DEFAULT_USER_ID set ---")
    os.environ["DEFAULT_USER_ID"] = "def_user"
    print(f"Result: {resolve_user_id()}")
    assert resolve_user_id() == "def_user"

    print("\n--- Test 3: COMPOSIO_USER_ID set (Priority) ---")
    os.environ["COMPOSIO_USER_ID"] = "comp_user"
    print(f"Result: {resolve_user_id()}")
    assert resolve_user_id() == "comp_user"

    print("\n--- Test 4: Explicit Override ---")
    print(f"Result: {resolve_user_id('explicit_user')}")
    assert resolve_user_id("explicit_user") == "explicit_user"

    print("\n✅ Verification Successful!")

if __name__ == "__main__":
    test_resolution()
