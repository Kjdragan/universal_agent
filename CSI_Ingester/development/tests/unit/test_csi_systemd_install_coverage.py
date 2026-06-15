"""Guard: every shipped CSI unit file is actually installed by the deploy.

csi-ingester.service silently rotted because it shipped as a unit file in
deployment/systemd/ but was EXEMPT from csi_install_systemd_extras.sh's install
loop (on the false premise that "a separate deploy step installs it"). Nothing
did, so the csi_run.sh wrapper that gives batch_brief its LLM key never reached
/etc/systemd/system and had to be installed by hand.

This test asserts the install list (CANONICAL_UNITS) covers every *.service /
*.timer file in deployment/systemd/, so a new or edited unit can't go
install-less again. EXEMPT_UNITS is only for pseudo-units not shipped as files
here (e.g. csi.target).
"""

from __future__ import annotations

from pathlib import Path
import re

DEV_DIR = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = DEV_DIR / "deployment" / "systemd"
INSTALLER = DEV_DIR / "scripts" / "csi_install_systemd_extras.sh"


def _parse_bash_array(text: str, name: str) -> set[str]:
    m = re.search(rf"{name}=\((.*?)\)", text, re.DOTALL)
    assert m, f"{name} array not found in {INSTALLER.name}"
    return {
        tok.strip()
        for tok in m.group(1).split()
        if tok.strip() and not tok.strip().startswith("#")
    }


def _shipped_unit_files() -> set[str]:
    return {
        p.name
        for p in SYSTEMD_DIR.iterdir()
        if p.suffix in (".service", ".timer")
    }


def test_every_shipped_unit_is_in_canonical_install_list() -> None:
    canonical = _parse_bash_array(INSTALLER.read_text(encoding="utf-8"), "CANONICAL_UNITS")
    shipped = _shipped_unit_files()
    missing = sorted(shipped - canonical)
    assert not missing, (
        "these unit files ship in deployment/systemd/ but are NOT in "
        f"CANONICAL_UNITS, so the deploy never installs them: {missing}"
    )


def test_csi_ingester_service_is_installed_by_deploy() -> None:
    canonical = _parse_bash_array(INSTALLER.read_text(encoding="utf-8"), "CANONICAL_UNITS")
    assert "csi-ingester.service" in canonical, (
        "csi-ingester.service must be in CANONICAL_UNITS so deploys keep "
        "/etc/systemd/system/csi-ingester.service in sync with the repo "
        "(it previously drifted because it was install-exempt)."
    )


def test_exempt_units_are_not_shipped_as_files() -> None:
    # EXEMPT_UNITS is for pseudo-units not shipped here; if an exempt unit is
    # also a file in deployment/systemd/ it should be CANONICAL (installed) instead.
    exempt = _parse_bash_array(INSTALLER.read_text(encoding="utf-8"), "EXEMPT_UNITS")
    shipped = _shipped_unit_files()
    overlap = sorted(exempt & shipped)
    assert not overlap, (
        f"these EXEMPT units ship as files and so should be CANONICAL: {overlap}"
    )
