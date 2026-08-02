"""Guard: the desktop SSHFS bridge must never go back into /etc/fstab.

Root cause of the recurring exit-124 production deploy timeout (2026-07-31 x2,
2026-08-02): ``systemd-fstab-generator`` runs on EVERY ``systemctl
daemon-reload`` and canonicalizes each fstab entry's mount point
(``chase()`` -> ``openat()`` -> ``statx()``). When the /home/kjdragan SSHFS
mount is *wedged* (desktop asleep/unreachable) that ``statx()`` blocks in
uninterruptible D state, systemd kills the generator sandbox at the hard 90s
``DEFAULT_TIMEOUT_USEC``, and every reload costs 90.3s instead of ~0.5s. A
deploy performs ~63 reloads, so the installer block runs ~95 min and
``timeout 30m ssh`` kills it.

Measured on uaonvps 2026-08-02 with the mount deliberately wedged:
``in /etc/fstab -> 90.42s`` vs ``native units -> 0.41s``.

The fix is a property of /etc/fstab's *contents*, not of any one code path, so
this guard is about writers: a single stray ``>> /etc/fstab`` anywhere silently
restores the stall. See scripts/install_vps_desktop_bridge.sh.
"""

from __future__ import annotations

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = REPO_ROOT / "deployment" / "systemd"
BRIDGE_INSTALLER = REPO_ROOT / "scripts" / "install_vps_desktop_bridge.sh"

# The eviction logic itself legitimately mentions both /etc/fstab and the bridge
# path; it is the only file allowed to.
_ALLOWED_FSTAB_WRITERS = {
    "scripts/install_vps_desktop_bridge.sh",
}

# `>> /etc/fstab` / `> /etc/fstab` / `tee /etc/fstab` — i.e. anything appending.
_FSTAB_WRITE = re.compile(r"(>>?\s*/etc/fstab|tee\s+(-a\s+)?/etc/fstab)")


def _tracked_shell_scripts() -> list[Path]:
    return [
        p
        for p in REPO_ROOT.rglob("*.sh")
        if ".git" not in p.parts
        and "node_modules" not in p.parts
        and "scratch_archive" not in p.parts
    ]


def test_bridge_units_exist_and_are_well_formed() -> None:
    mount_unit = SYSTEMD_DIR / "home-kjdragan.mount"
    automount_unit = SYSTEMD_DIR / "home-kjdragan.automount"

    assert mount_unit.is_file(), f"missing {mount_unit.relative_to(REPO_ROOT)}"
    assert automount_unit.is_file(), f"missing {automount_unit.relative_to(REPO_ROOT)}"

    mount_text = mount_unit.read_text()
    assert "Where=/home/kjdragan" in mount_text
    assert "Type=fuse.sshfs" in mount_text
    assert "TimeoutSec=" in mount_text, "an unbounded mount attempt can hang the unit"
    # Peer coordinates stay templated so the operator's machine identity is
    # config rendered by the installer, not committed repo content.
    assert "__BRIDGE_PEER__" in mount_text
    assert "__BRIDGE_USER__" in mount_text

    automount_text = automount_unit.read_text()
    assert "Where=/home/kjdragan" in automount_text
    # A non-zero idle timeout periodically unmounts a possibly-wedged FUSE
    # mount -- exactly what killed the bridge on 07-31 and 08-01.
    assert "TimeoutIdleSec=0" in automount_text


def test_no_repo_script_writes_the_bridge_into_fstab() -> None:
    """No script may append an /etc/fstab entry for the bridge path."""
    offenders: list[str] = []
    for script in _tracked_shell_scripts():
        rel = script.relative_to(REPO_ROOT).as_posix()
        if rel in _ALLOWED_FSTAB_WRITERS:
            continue
        text = script.read_text(errors="replace")
        if not _FSTAB_WRITE.search(text):
            continue
        # Writing to fstab at all is allowed (e.g. swap); naming the bridge is not.
        if "/home/kjdragan" in text or "fuse.sshfs" in text:
            offenders.append(rel)

    assert not offenders, (
        "these scripts write a desktop-bridge entry into /etc/fstab, which "
        "reintroduces the 90s daemon-reload stall and the exit-124 deploy "
        f"timeout: {offenders}"
    )


def test_bridge_installer_evicts_fstab_and_runs_before_the_installer_block() -> None:
    assert BRIDGE_INSTALLER.is_file()
    installer = BRIDGE_INSTALLER.read_text()
    # It must actually strip, not merely warn.
    assert "sed -i" in installer and "/etc/fstab" in installer
    assert "cp -a /etc/fstab" in installer, "must back up before editing fstab"

    deploy = (REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh").read_text()
    assert "install_vps_desktop_bridge.sh" in deploy, "bridge installer is not wired in"

    # Ordering is load-bearing: fstab must be clean before the first reload of
    # the systemd installer block.
    bridge_at = deploy.index("install_vps_desktop_bridge.sh")
    units_at = deploy.index("install_vps_systemd_units.sh")
    assert bridge_at < units_at, (
        "install_vps_desktop_bridge.sh must run BEFORE install_vps_systemd_units.sh "
        "so /etc/fstab is already clean by the first daemon-reload"
    )


def test_deploy_has_a_reload_canary() -> None:
    """A slow reload must produce a signal instead of a silent 30-min timeout."""
    deploy = (REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh").read_text()
    assert "[reload-canary]" in deploy
    assert "::warning::" in deploy


def test_unwedge_script_never_stats_the_bridge_path() -> None:
    """A hung FUSE stat is uninterruptible; `timeout` does not bound it.

    Probing the bridge with stat/ls/test would hang the watchdog harder than
    the wedge it is meant to fix.
    """
    script = REPO_ROOT / "scripts" / "vps_bridge_unwedge.sh"
    assert script.is_file()
    text = script.read_text()

    # The detector must be the kernel counter, not a filesystem call.
    assert "/sys/fs/fuse/connections" in text
    assert "waiting" in text
    # Aborting the connection is the only primitive that frees D-state waiters.
    assert "abort" in text

    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("stat -t /home/kjdragan", "ls /home/kjdragan", "umount -l"):
        assert forbidden not in code, (
            f"{forbidden!r} in vps_bridge_unwedge.sh: this either blocks "
            "uninterruptibly or leaves waiting>0 and a non-empty cgroup "
            "(the twice-failed manual fix)"
        )
