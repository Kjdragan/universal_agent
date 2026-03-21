"""Tests for VP profile resolution with soul file fields.

Covers: VpProfile.soul_file, resolve_vp_profiles(), display_name
"""
from __future__ import annotations

from pathlib import Path

from universal_agent.vp.profiles import resolve_vp_profiles


def test_profiles_include_soul_file_field():
    """Both VP profiles must have a soul_file set."""
    profiles = resolve_vp_profiles()
    for vp_id, profile in profiles.items():
        assert profile.soul_file, f"Profile {vp_id} missing soul_file"
        assert profile.soul_file.endswith(".md"), (
            f"Profile {vp_id} soul_file '{profile.soul_file}' should be a .md file"
        )


def test_coder_profile_soul_is_codie():
    """vp.coder.primary profile must reference CODIE_SOUL.md."""
    profiles = resolve_vp_profiles()
    coder = profiles.get("vp.coder.primary")
    assert coder is not None, "vp.coder.primary profile must exist"
    assert coder.soul_file == "CODIE_SOUL.md"


def test_general_profile_soul_is_atlas():
    """vp.general.primary profile must reference ATLAS_SOUL.md."""
    profiles = resolve_vp_profiles()
    general = profiles.get("vp.general.primary")
    assert general is not None, "vp.general.primary profile must exist"
    assert general.soul_file == "ATLAS_SOUL.md"


def test_general_profile_display_name_is_atlas():
    """VP General display name should be ATLAS, not GENERALIST."""
    profiles = resolve_vp_profiles()
    general = profiles["vp.general.primary"]
    assert general.display_name == "ATLAS", (
        f"Expected 'ATLAS' but got '{general.display_name}'"
    )


def test_soul_files_exist_on_disk():
    """Both soul files must exist at the expected prompt_assets path."""
    prompt_assets_dir = Path(__file__).resolve().parents[2] / "src" / "universal_agent" / "prompt_assets"
    profiles = resolve_vp_profiles()
    for vp_id, profile in profiles.items():
        soul_path = prompt_assets_dir / profile.soul_file
        assert soul_path.exists(), (
            f"Soul file for {vp_id} not found at {soul_path}"
        )
