"""Auto-remediation dispatcher for ``deslop-findings`` issues (Theme 2 v1a).

Mirrors ``ci_failure_grace_recheck.py``: a synchronous, ``gh``-driven poller that
triages open ``deslop-findings`` issues and — for the SAFE, behavior-preserving
class — dispatches a Cody fix mission against the activity DB. Everything else is
escalated (draft + email / Telegram), never auto-applied.

**Prime safety rule (operator's words):** *"those fixes have to be super verified
so that we don't have the case where a broken fix is applied."* A wrong auto-applied
fix to prod is worse than no fix. The triage gate below therefore defaults to
caution: the never-auto path list is deliberately **over-broad** (a false positive
costs a human review; a false negative could auto-merge a catastrophic change), and
v1 ships in ``observe`` mode where *every* dispatch is a DRAFT PR + email — never an
auto-merge — until real runs prove the machinery.

Decision flow (per open ``deslop-findings`` issue; all reads via the VPS ``gh`` CLI):

  1. Issue already ``deslop-dispatched`` / ``needs-operator``?  -> skip (claimed)
  2. Parse the finding bullets out of the issue body.
  3. Classify the issue by the files its findings touch:
       - any unparseable file path / no findings        -> needs_operator (escalate)
       - any file on the HARD never-auto list (§3)       -> never_auto (dispatch DRAFT+email)
       - all files safe, behavior-preserving             -> tier_a_safe (dispatchable)
  4. Resolve delivery by mode (``UA_DESLOP_AUTOREMEDIATE_MODE``, default ``observe``):
       - observe: tier_a_safe AND never_auto both -> DRAFT PR + email (never auto-merge)
       - auto:    tier_a_safe -> non-draft PR (repo auto-merges on green); never_auto
                  STILL DRAFT + email regardless of confidence.
  5. Source-PR gate (``decide_source_pr_disposition``, kill switch
     ``UA_DESLOP_CHECK_SOURCE_PR``): the slop only reaches ``main`` once the source PR
     MERGES. So before dispatching a fix off ``main``, check the source PR's state:
       - MERGED / unknown -> proceed (dispatch as below — the slop is on ``main``)
       - OPEN             -> ``defer_to_open_source_pr``: post a one-time advisory
                             comment ON THE PR (amend before merge), do NOT dispatch;
                             leave the issue unlabeled so a later merge re-triggers it.
       - CLOSED unmerged  -> ``close_moot_issue``: the slop never landed -> close it.
  6. Dispatch: claim the issue (``deslop-dispatched``), queue a Cody mission whose
     brief reproduces the finding -> applies ONLY the behavior-preserving fix ->
     runs the full CI gate locally -> opens the PR (draft in observe mode). Email Kevin.
  7. Escalate: label ``needs-operator`` + Telegram-ping (uncertain / not-a-code-fix).

Unlike the CI auto-fix (which pushes to an *existing* PR branch), once a deslop
finding's source PR has merged the slop lives in ``main``, so the Cody mission opens a
**fresh PR off origin/main**. The step-5 gate exists because that merge is not a given:
auto-merge branches (``claude/*`` etc.) merge within minutes, but manual-review branches
(``codie/*`` / ``kevin/*`` / ``feature/*``) can sit open for hours — fixing off ``main``
then would target slop that isn't there yet (issue #933 / open PR #932 was the case
that motivated this gate).

Like the precedent this module is intentionally **synchronous** (no asyncio at import
or call time). GitHub access is subprocess ``gh``; email is ``send_simone_email``;
Telegram is ``telegram_send_sync``; Cody dispatch is ``queue_proactive_task`` against
the activity DB. It is **advisory (exit 0)** — but per the hard-won lesson, fire it
end-to-end and inspect the JSON output; ``--dry-run`` triages without side effects.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
import re
import subprocess
from typing import Any, Callable, Optional
import unicodedata

from universal_agent import task_hub

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_REPO = "Kjdragan/universal_agent"

LABEL_FINDINGS = "deslop-findings"
LABEL_DISPATCHED = "deslop-dispatched"
LABEL_NEEDS_OPERATOR = "needs-operator"

MODE_OBSERVE = "observe"
MODE_AUTO = "auto"
DEFAULT_MODE = MODE_OBSERVE

# Delivery kinds.
DELIVERY_DRAFT_EMAIL = "draft_email"  # DRAFT PR + email Kevin; never auto-merges
DELIVERY_AUTO_MERGE = "auto_merge"  # non-draft PR; repo auto-merges on green CI

# Classifications.
CLASS_TIER_A = "tier_a_safe"
CLASS_NEVER_AUTO = "never_auto"
CLASS_NEEDS_OPERATOR = "needs_operator"

# Source-PR dispositions (how a would-be dispatch is re-routed by the source
# PR's merge state — see decide_source_pr_disposition).
SRC_PR_PROCEED = "proceed"  # slop is on main (PR merged / state unknown) -> dispatch as before
SRC_PR_DEFER = "defer_open_pr"  # PR still open -> comment there, don't fix off-main
SRC_PR_CLOSE_MOOT = "close_moot"  # PR closed unmerged -> slop never landed -> close the issue

# A hidden marker so the open-PR advisory comment is posted at most once.
_DEFER_MARKER = "<!-- deslop-defer:issue-{n} -->"


def _notify_operator() -> bool:
    """Whether the deslop dispatcher may ping the operator (email / Telegram).

    Default OFF: deslop activity is already durably recorded (the
    ``deslop-dispatched`` / ``needs-operator`` label, the Task Hub row, the GitHub
    issue + PR), so an agent can always see it. The operator inbox stays quiet
    unless ``UA_DESLOP_NOTIFY_OPERATOR`` is explicitly truthy.
    """
    return os.getenv("UA_DESLOP_NOTIFY_OPERATOR", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


# --------------------------------------------------------------------------- #
# HARD never-auto gate (§3) — the safety-critical heart.
#
# Deliberately OVER-BROAD: any finding whose file matches forces draft+email and
# is *never* auto-merged, even in ``auto`` mode and even if "confident". A false
# positive (over-catch) only costs a human review; a false negative (a deploy /
# secret / schema / CI file that slips through to auto-merge) is catastrophic and
# self-perpetuating (a bad ``remote_deploy.sh`` breaks *all* future deploys).
#
# Matching is on a normalized, lower-cased, POSIX path. When in doubt -> True.
# --------------------------------------------------------------------------- #


def _normalize_path(path: str, *, fold_case: bool = True) -> str:
    """POSIX path with ``./`` prefixes, backslashes, repeated slashes and
    surrounding noise stripped. Normalization is aggressive on purpose so that
    obfuscated finding paths (``scripts//deploy/x``, ``.\\X``, trailing space,
    backtick-wrapped) cannot evade the gate. ``fold_case`` lower-cases for
    matching; pass ``False`` to preserve case for filesystem resolution."""
    p = path or ""
    # Strip zero-width / format / control chars and apply compatibility folding
    # (fullwidth -> ASCII) so unicode obfuscation cannot evade substring checks.
    p = "".join(ch for ch in p if unicodedata.category(ch) not in ("Cf", "Cc"))
    p = unicodedata.normalize("NFKC", p)
    p = p.strip().strip("`").strip()
    p = p.replace("\\", "/")
    p = re.sub(r"/+", "/", p)  # collapse repeated slashes (a//b -> a/b)
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    return p.lower() if fold_case else p


# Suffixes layered on top of a real filename (rendered/templated units, env
# samples). Stripped before unit-type matching so ``api.service.template`` and
# ``foo.timer.j2`` are still recognised as systemd units.
_TEMPLATE_SUFFIXES = (".template", ".tmpl", ".j2", ".jinja", ".jinja2", ".in", ".dist")

# systemd unit suffixes — the {.service,.timer,.socket,.mount} allowlist alone
# was too narrow (.target/.path/.slice/.automount/.scope are units too).
_UNIT_SUFFIXES = (
    ".service", ".timer", ".socket", ".mount", ".automount", ".target",
    ".path", ".slice", ".scope", ".swap", ".device",
)

# SQLite / embedded-DB extensions (after stripping -wal/-shm/-journal sidecars).
_DB_SUFFIXES = (
    ".db", ".db3", ".sqlite", ".sqlite2", ".sqlite3", ".s3db", ".sl3",
    ".sdb", ".sqlitedb", ".duckdb", ".ddb",
)

# Private-key / keystore / cert material — squarely "secrets".
_KEY_SUFFIXES = (
    ".pem", ".key", ".crt", ".cer", ".p12", ".pfx", ".jks", ".keystore",
    ".asc", ".gpg", ".kdbx", ".ppk",
)

# Credential files / key material matched by exact basename.
_CREDENTIAL_BASENAMES = frozenset({
    ".netrc", ".npmrc", ".pypirc", ".dockercfg", ".pgpass", ".htpasswd",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "deploy_key",
})

# Deploy/release verbs naming a production-mutating script.
_DEPLOY_VERB_RE = re.compile(
    r"(^|[_-])(deploy|redeploy|ship|release|rollout|promote|publish)([_\-.]|$)"
)
_SCRIPT_EXTS = (".sh", ".bash", ".zsh", ".ksh", ".fish")
_SCRIPT_DIRS = frozenset({"scripts", "bin", "ops", "scratch", "ci", ".ci", "deploy", "tools"})
# Source extensions where a "token"/verb substring is almost certainly code, not
# a secret/deploy artifact (avoids over-catching token-COUNTING modules).
_CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".md", ".rst")

# Embedded-DDL markers — for the content scan (the only robust way to catch
# schema-owning modules like task_hub.py that no path heuristic can identify).
_DDL_RE = re.compile(
    r"\b(create\s+(unique\s+)?(table|index|trigger|view|virtual\s+table)|alter\s+table)\b",
    re.IGNORECASE,
)

# Host control-plane markers — a non-shell file (e.g. a .py) that drives systemd /
# the host install plane is as dangerous as a deploy script. Catches it by content.
_CONTROL_PLANE_RE = re.compile(
    r"(systemctl\b|/etc/systemd|systemd/system|loginctl\s+enable-linger"
    r"|tailscale\s+serve)",
    re.IGNORECASE,
)


def _strip_template_suffix(base: str) -> str:
    changed = True
    while changed:
        changed = False
        for suf in _TEMPLATE_SUFFIXES:
            if base.endswith(suf):
                base = base[: -len(suf)]
                changed = True
    return base


def is_never_auto(path: str) -> bool:
    """Return True if a fix touching ``path`` must NEVER auto-merge (draft+email
    at most). Deliberately OVER-BROAD — a false positive only costs a human
    review; a false negative could auto-merge a catastrophic change. Hardened
    against the false negatives found by adversarial verification (deploy-verb
    scripts, ``migrate_*`` files, key material, env-in-the-middle names, the full
    systemd unit-type set + templated units + drop-ins, sqlite sidecars/alt
    extensions, and double-slash obfuscation)."""
    p = _normalize_path(path)
    if not p:
        # Unknown/empty path: cannot prove it is safe -> treat as never-auto.
        return True

    segments = p.split("/")
    base = segments[-1]
    dirs = set(segments[:-1])
    unit_base = _strip_template_suffix(base)

    # --- CI / deploy / control-plane locations (whole directories) ---
    if any(seg == ".github" for seg in segments):
        return True
    if "systemd" in dirs or "deployment" in dirs or "deploy" in dirs:
        return True
    if "scripts/deploy/" in f"/{p}":
        return True
    # systemd drop-in override dir: foo.service.d/override.conf
    if re.search(r"\.(service|timer|socket|mount|target|path|slice|scope|automount|swap)\.d/", f"/{p}/"):
        return True

    # --- shell scripts are inherently prod-mutating (deploy / install / systemd
    # / host ops) — treat ALL of them as never-auto. This single rule closes the
    # systemd-installer class (install_vps_*.sh, *_install_systemd_extras.sh) that
    # a verb/dir heuristic kept missing. Over-broad by design.
    if base.endswith(_SCRIPT_EXTS):
        return True

    # --- deploy / ship / release scripts (non-shell, e.g. deploy_to_vps.py) ---
    if "deploy" in base:  # covers deploy*, remote_deploy*, *deploy*.py
        return True
    if _DEPLOY_VERB_RE.search(base):
        script_ish = "." not in base or bool(_SCRIPT_DIRS & dirs)
        if script_ish:
            return True

    # --- secrets / Infisical / env / credentials / key material ---
    if "infisical" in p or "secret" in p or "credential" in p or "vault" in base:
        return True
    if "service-account" in base or "service_account" in base:
        return True
    if ".env" in base or base.startswith("env.") or base == "env" or "dotenv" in base:
        return True
    if unit_base.endswith(_KEY_SUFFIXES) or base.endswith(_KEY_SUFFIXES):
        return True
    if base in _CREDENTIAL_BASENAMES:
        return True
    if "token" in base and not base.endswith(_CODE_EXTS):
        return True

    # --- DB schema / migrations ---
    if "/migrations/" in f"/{p}" or p.startswith("migrations/") or "alembic/versions/" in f"/{p}":
        return True
    if "migrat" in p:  # migrate / migration / migrating
        return True
    if "schema" in base:
        return True
    if base.endswith((".sql", ".ddl")):
        return True
    # Numbered / Flyway-style migration filenames (0014_add_index.py, V2__add.sql).
    if re.match(r"^(v\d+__|\d{2,}[_\-]).*\.(py|sql|sh|rb)$", base):
        return True

    # --- cross-host / embedded DB files (incl. -wal/-shm/-journal sidecars) ---
    db_base = re.sub(r"-(wal|shm|journal)$", "", base)
    if db_base.endswith(_DB_SUFFIXES):
        return True
    if "csi.db" in p or "activity_state.db" in p or "runtime_state.db" in p:
        return True

    # --- systemd unit files (after stripping template/rendered suffixes) ---
    if unit_base.endswith(_UNIT_SUFFIXES) or base.endswith(_UNIT_SUFFIXES):
        return True

    return False


def _scan_file(abs_path: str, pattern: re.Pattern, *, max_bytes: int = 400_000) -> bool:
    """Best-effort content match; never raises (unreadable -> False)."""
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            return bool(pattern.search(fh.read(max_bytes)))
    except (OSError, ValueError):
        return False


def file_has_ddl(abs_path: str, *, max_bytes: int = 400_000) -> bool:
    """True if the file embeds SQL DDL (CREATE/ALTER TABLE/INDEX/...). Catches
    schema-owning modules (e.g. ``task_hub.py``, ``*_db.py``, ``storage.py``)
    that no path heuristic can reliably identify."""
    return _scan_file(abs_path, _DDL_RE, max_bytes=max_bytes)


def file_touches_control_plane(abs_path: str, *, max_bytes: int = 400_000) -> bool:
    """True if the file drives systemd / the host install plane / tailscale —
    a deploy-adjacent operation dangerous to auto-apply, even from a non-shell
    file (the shell-extension gate already covers ``*.sh``)."""
    return _scan_file(abs_path, _CONTROL_PLANE_RE, max_bytes=max_bytes)


def _resolve_under_root(root: str, rel: str) -> Optional[str]:
    """Resolve a finding-relative path under ``root``, refusing escapes."""
    try:
        root_abs = os.path.abspath(root)
        cand = os.path.normpath(os.path.join(root_abs, rel))
        if os.path.commonpath([root_abs, cand]) != root_abs:
            return None
        return cand
    except (ValueError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    """One parsed bullet from a deslop advisory issue body."""

    file: str
    severity: str = "?"
    issue: str = ""
    fix: str = ""


@dataclass
class DeslopIssue:
    """An open ``deslop-findings`` issue as gathered from ``gh``."""

    number: int
    title: str = ""
    body: str = ""
    labels: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    source_pr: Optional[int] = None

    @property
    def files(self) -> list[str]:
        return [f.file for f in self.findings]


# --------------------------------------------------------------------------- #
# Pure parsing + decision logic (unit-tested without touching the network)
# --------------------------------------------------------------------------- #

# A finding bullet:  `- 🟡 **[medium]** `path/to/file.py` — issue text`
_FINDING_RE = re.compile(
    r"^\s*-\s*[^\w`*]*\*\*\[(?P<sev>high|medium|low|\?)\]\*\*\s*`(?P<file>[^`]+)`"
    r"\s*[—–\-]+\s*(?P<issue>.*?)\s*$"
)
# The optional fix sub-bullet on the following line:  `  - _fix_: ...`
_FIX_RE = re.compile(r"^\s*-\s*_fix_:\s*(?P<fix>.*?)\s*$")
_SOURCE_PR_RE = re.compile(r"Tracked from PR #(?P<pr>\d+)")


def parse_findings(body: str) -> list[Finding]:
    """Extract the finding bullets from an advisory issue body. Robust to a
    missing icon / em-dash variant / absent fix line."""
    findings: list[Finding] = []
    for line in (body or "").splitlines():
        m = _FINDING_RE.match(line)
        if m:
            findings.append(
                Finding(
                    file=m.group("file").strip(),
                    severity=m.group("sev").strip().lower(),
                    issue=m.group("issue").strip(),
                )
            )
            continue
        fm = _FIX_RE.match(line)
        if fm and findings:
            # Attach to the most recent finding (fix follows its finding line).
            if not findings[-1].fix:
                findings[-1].fix = fm.group("fix").strip()
    return findings


def parse_source_pr(body: str) -> Optional[int]:
    m = _SOURCE_PR_RE.search(body or "")
    if m:
        try:
            return int(m.group("pr"))
        except ValueError:
            return None
    return None


def classify_issue(issue: DeslopIssue, *, root: Optional[str] = None) -> tuple[str, str]:
    """Classify a whole issue by the files its findings touch. The whole issue
    inherits the most-cautious classification of any single finding, because a
    PR fixing the issue may touch all of them.

    ``root`` (the codebase root, present in prod) enables a content scan that
    upgrades a path-safe file to never-auto if it embeds SQL DDL — the robust
    catch for schema-owning modules. It only ever ADDS never-auto, never removes.

    Returns ``(classification, reason)``."""
    if not issue.findings:
        return CLASS_NEEDS_OPERATOR, "no_parseable_findings"

    files = issue.files
    # An unparseable / empty path means we cannot prove safety -> human.
    if any(not _normalize_path(f) for f in files):
        return CLASS_NEEDS_OPERATOR, "unparseable_file_path"

    # ANY never-auto file (path gate) taints the whole issue.
    tainted = [f for f in files if is_never_auto(f)]
    if tainted:
        return CLASS_NEVER_AUTO, f"touches_never_auto_path:{tainted[0]}"

    # Content scan (best-effort, prod): a path-safe file that embeds SQL DDL or
    # drives the host control plane is upgraded to never-auto.
    if root:
        for f in files:
            abs_path = _resolve_under_root(root, _normalize_path(f, fold_case=False))
            if not (abs_path and os.path.isfile(abs_path)):
                continue
            if file_has_ddl(abs_path):
                return CLASS_NEVER_AUTO, f"embedded_ddl:{f}"
            if file_touches_control_plane(abs_path):
                return CLASS_NEVER_AUTO, f"control_plane_ops:{f}"

    return CLASS_TIER_A, "safe_behavior_preserving"


def resolve_delivery(classification: str, mode: str) -> tuple[str, Optional[str], str]:
    """Map ``(classification, mode)`` to ``(action, delivery, reason)``.

    action ∈ ``skip`` | ``dispatch`` | ``escalate``.
    delivery ∈ ``draft_email`` | ``auto_merge`` | ``None`` (escalate/skip).

    INVARIANT: ``observe`` mode and the ``never_auto`` class NEVER yield
    ``auto_merge`` — they are always ``draft_email``.
    """
    if classification == CLASS_NEEDS_OPERATOR:
        return "escalate", None, "needs_operator"
    if classification == CLASS_NEVER_AUTO:
        # Never auto-merge regardless of mode/confidence.
        return "dispatch", DELIVERY_DRAFT_EMAIL, "never_auto_draft_email"
    if classification == CLASS_TIER_A:
        if mode == MODE_AUTO:
            return "dispatch", DELIVERY_AUTO_MERGE, "tier_a_auto"
        return "dispatch", DELIVERY_DRAFT_EMAIL, "tier_a_observe_draft_email"
    # Unknown classification -> safest path.
    return "escalate", None, f"unknown_classification:{classification}"


def decide_action(
    issue: DeslopIssue, mode: str, *, root: Optional[str] = None
) -> tuple[str, Optional[str], str]:
    """Full per-issue gate. Returns ``(action, delivery, reason)``.

    Idempotency first: an already-claimed issue is skipped (another dispatcher
    won, or a human is already on it)."""
    if LABEL_DISPATCHED in issue.labels:
        return "skip", None, "already_dispatched"
    if LABEL_NEEDS_OPERATOR in issue.labels:
        return "skip", None, "already_escalated"

    classification, class_reason = classify_issue(issue, root=root)
    action, delivery, action_reason = resolve_delivery(classification, mode)
    return action, delivery, f"{class_reason}->{action_reason}"


def _check_source_pr_enabled() -> bool:
    """Kill switch for the source-PR-state gate (default on)."""
    return os.getenv("UA_DESLOP_CHECK_SOURCE_PR", "1").strip().lower() in {"1", "true", "yes"}


def decide_source_pr_disposition(pr_state: Optional[dict]) -> tuple[str, str]:
    """Decide how a would-be dispatch should be re-routed given the source PR's
    state. Returns ``(disposition, reason)``.

    A deslop finding's slop only reaches ``main`` once its source PR MERGES. For
    manual-review branches (``codie/*`` / ``kevin/*`` / ``feature/*``) the PR can
    sit OPEN for hours, so a fix-off-main mission would target slop that isn't
    there yet (issue #933 / open PR #932 was the motivating case). A CLOSED-
    unmerged PR means the slop never landed at all, so the finding is moot.

    Unknown/empty state -> ``proceed`` (the safe default = pre-existing behavior).
    """
    if not pr_state:
        return SRC_PR_PROCEED, "source_pr_state_unknown"
    state = str(pr_state.get("state") or "").upper()
    if state == "MERGED" or pr_state.get("merged"):
        return SRC_PR_PROCEED, "source_pr_merged"
    if state == "OPEN":
        return SRC_PR_DEFER, "source_pr_open"
    if state == "CLOSED":
        return SRC_PR_CLOSE_MOOT, "source_pr_closed_unmerged"
    return SRC_PR_PROCEED, f"source_pr_state:{state.lower()}"


# --------------------------------------------------------------------------- #
# GitHub access (subprocess `gh`) — injectable for tests
# --------------------------------------------------------------------------- #

GhRunner = Callable[[list[str]], "tuple[int, str, str]"]


def _gh_subprocess_env() -> dict:
    """Env for the `gh` subprocess. ``initialize_runtime_secrets()`` can inject an
    Infisical ``GH_TOKEN`` that is expired/scopeless (seen: 401 Bad credentials),
    and gh prefers an env token over its ``~/.config/gh`` login — so a bad env
    token silently 401s every gh call (-> empty issue list -> silent no-op). Drop
    the env token so gh uses the box's maintained ``gh auth login``, but ONLY when
    that config login exists, so we never strip the sole credential (e.g. in CI,
    where GITHUB_TOKEN is the only auth)."""
    env = dict(os.environ)
    cfg_dir = os.environ.get("GH_CONFIG_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "gh"
    )
    if os.path.isfile(os.path.join(cfg_dir, "hosts.yml")):
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
            env.pop(key, None)
    return env


def _real_gh(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_gh_subprocess_env(),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "gh CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"gh timed out after {timeout}s: {' '.join(args)}"


def list_open_finding_issue_numbers(
    repo: str, gh: GhRunner, limit: int = 50
) -> list[int]:
    """Open ``deslop-findings`` issue numbers, newest first."""
    rc, out, _ = gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            LABEL_FINDINGS,
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            str(limit),
        ]
    )
    if rc != 0:
        return []
    try:
        rows = json.loads(out or "[]")
    except json.JSONDecodeError:
        return []
    return [int(r["number"]) for r in rows if "number" in r]


def fetch_issue(repo: str, number: int, gh: GhRunner) -> Optional[DeslopIssue]:
    rc, out, _ = gh(
        [
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,title,body,labels",
        ]
    )
    if rc != 0:
        return None
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return None
    body = str(data.get("body") or "")
    issue = DeslopIssue(
        number=int(data.get("number") or number),
        title=str(data.get("title") or ""),
        body=body,
        labels=[str(lbl.get("name", "")) for lbl in (data.get("labels") or [])],
        findings=parse_findings(body),
        source_pr=parse_source_pr(body),
    )
    return issue


def fetch_pr_state(repo: str, number: int, gh: GhRunner) -> Optional[dict]:
    """Return ``{"state": "OPEN"|"CLOSED"|"MERGED", "merged": bool}`` for the
    source PR, or ``None`` if it can't be determined (treated as ``proceed``)."""
    rc, out, _ = gh(["pr", "view", str(number), "--repo", repo, "--json", "state,mergedAt"])
    if rc != 0:
        return None
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return None
    state = str(data.get("state") or "").upper()
    if not state:
        return None
    return {"state": state, "merged": bool(data.get("mergedAt"))}


def _pr_has_deslop_comment(repo: str, pr_number: int, marker: str, gh: GhRunner) -> bool:
    """True if the source PR already carries this issue's defer-advisory comment
    (idempotency guard so the poller never re-comments every run)."""
    rc, out, _ = gh(["pr", "view", str(pr_number), "--repo", repo, "--json", "comments"])
    if rc != 0:
        return False
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return False
    return any(marker in str(c.get("body") or "") for c in (data.get("comments") or []))


def defer_to_open_source_pr(
    issue: DeslopIssue, *, repo: str = DEFAULT_REPO, gh: GhRunner = _real_gh
) -> dict[str, Any]:
    """The source PR is still open: post a one-time advisory comment ON THE PR so
    the slop can be amended before merge, instead of dispatching a fix off ``main``
    (where the slop doesn't exist yet). The deslop issue is intentionally left
    unlabeled so the next poll re-evaluates it — when the PR finally merges (slop
    on main) it dispatches for real, or when it closes unmerged it is closed moot."""
    pr = issue.source_pr
    marker = _DEFER_MARKER.format(n=issue.number)
    if pr is None:
        return {"action": SRC_PR_DEFER, "commented": False, "reason": "no_source_pr"}
    if _pr_has_deslop_comment(repo, pr, marker, gh):
        return {"action": SRC_PR_DEFER, "commented": False, "source_pr": pr, "reason": "already_commented"}

    bullets = "\n".join(
        f"- `{f.file}` — {f.issue}" + (f"\n  - _fix_: {f.fix}" if f.fix else "")
        for f in issue.findings
    )
    body = (
        f"{marker}\n"
        f"🧹 **Deslop advisory** ([#{issue.number}](https://github.com/{repo}/issues/{issue.number})) "
        f"flagged behavior-preserving cleanups in this PR's changes. Since this PR is still **open**, "
        f"the cleanest fix is to **amend it here before merge** rather than a follow-up PR off `main`:\n\n"
        f"{bullets}\n\n"
        f"_Advisory only — never blocks this PR. Auto-posted by `deslop_remediation_dispatch`. "
        f"If the PR merges with the slop intact, the finding is picked up off `main` instead._"
    )
    rc, _, _ = gh(["pr", "comment", str(pr), "--repo", repo, "--body", body])
    return {"action": SRC_PR_DEFER, "commented": rc == 0, "source_pr": pr}


def close_moot_issue(
    issue: DeslopIssue, reason: str, *, repo: str = DEFAULT_REPO, gh: GhRunner = _real_gh
) -> dict[str, Any]:
    """The source PR was closed WITHOUT merging, so the flagged slop never reached
    ``main`` — close the deslop issue as moot (mirrors the ci-failure auto-close)."""
    comment = (
        f"Auto-closed by `deslop_remediation_dispatch`: source PR #{issue.source_pr} was closed "
        f"without merging, so the flagged slop never reached `main`. Nothing to remediate ({reason})."
    )
    rc, _, _ = gh(["issue", "close", str(issue.number), "--repo", repo, "--comment", comment])
    return {"action": SRC_PR_CLOSE_MOOT, "closed": rc == 0, "source_pr": issue.source_pr, "reason": reason}


# Color + description for the labels the dispatcher manages, so it can create
# them on demand. `gh issue edit --add-label` FAILS on a label that does not
# exist in the repo (gh never auto-creates), which silently broke the claim and
# made the poller re-dispatch + re-email every run. We ensure the label exists
# (idempotent `gh label create --force`) before adding it.
_MANAGED_LABELS = {
    LABEL_DISPATCHED: ("0E8A16", "deslop-findings issue claimed by the auto-remediation dispatcher"),
    LABEL_NEEDS_OPERATOR: ("B60205", "Escalated to the operator for manual handling"),
}


def _ensure_label(repo: str, label: str, gh: GhRunner) -> None:
    """Best-effort: make sure ``label`` exists before adding it. ``--force`` makes
    this idempotent (create-or-update, never errors if it already exists)."""
    color, desc = _MANAGED_LABELS.get(label, ("ededed", ""))
    gh(["label", "create", label, "--repo", repo, "--color", color, "--description", desc, "--force"])


def _add_label(repo: str, number: int, label: str, gh: GhRunner) -> bool:
    _ensure_label(repo, label, gh)
    rc, _, _ = gh(["issue", "edit", str(number), "--repo", repo, "--add-label", label])
    return rc == 0


# --------------------------------------------------------------------------- #
# Cody fix-mission brief
# --------------------------------------------------------------------------- #


def build_cody_brief(issue: DeslopIssue, delivery: str, *, repo: str = DEFAULT_REPO) -> str:
    """The mission brief. STRICT contract: reproduce the finding, apply ONLY the
    behavior-preserving fix, run the full local gate, super-verify, open the PR.

    ``delivery`` controls the final PR posture:
      - ``draft_email``: open a **DRAFT** PR on a ``deslop/observe-fix-*`` branch.
        That prefix is EXCLUDED from pr-auto-merge.yml, so the draft bit is no
        longer the only thing keeping a behavior-preserving comment edit out of
        prod (CI can't judge deslop quality) — a human reviews and merges it.
      - ``auto_merge``:  open a **non-draft** ``claude/deslop-fix-*`` PR so
        pr-auto-merge.yml lands it once CI is green (triage said every file SAFE).
    """
    draft = delivery == DELIVERY_DRAFT_EMAIL
    findings_md = "\n".join(
        f"- `{f.file}` [{f.severity}]: {f.issue}" + (f"\n    fix: {f.fix}" if f.fix else "")
        for f in issue.findings
    )
    # Observe-mode fixes go on a `deslop/observe-fix-*` prefix that pr-auto-merge.yml
    # EXCLUDES, so a deslop cleanup can never reach prod on a stray `gh pr ready` — a
    # human must merge it (CI is blind to behavior-preserving comment edits). Auto-merge
    # mode (triage classified every touched file SAFE) stays on `claude/deslop-fix-*`,
    # which auto-merges on green CI.
    branch = (
        f"deslop/observe-fix-issue-{issue.number}"
        if draft
        else f"claude/deslop-fix-issue-{issue.number}"
    )
    pr_posture = (
        "Open the PR as a **DRAFT** (`gh pr create --draft`). This "
        "`deslop/observe-fix-*` branch is EXCLUDED from pr-auto-merge.yml, so even if "
        "the draft is later marked ready it will NOT auto-merge — a human must review "
        "and merge it. Do NOT mark it ready yourself."
        if draft
        else (
            "Open a **non-draft** PR (`gh pr create`). On this `claude/deslop-fix-*` "
            "branch pr-auto-merge.yml lands it once `pr-validate` is green. Only do "
            "this because triage classified every touched file as SAFE."
        )
    )
    lines = [
        f"Behavior-preserving deslop fix for issue #{issue.number}"
        f" (\"{issue.title}\"). Apply ONLY the suggested cleanups below — nothing else.",
        "",
        "## The findings to fix",
        findings_md or "(see the issue body)",
        "",
        "## STEP 0 — RE-VERIFY OR NO-OP (do this FIRST, before any edit)",
        f"- Run: `gh issue view {issue.number} --repo {repo} --json state,labels`. If the",
        "  issue is CLOSED, STOP: write COMPLETION.md noting 'no-op: issue already resolved'.",
        "- Read each cited file and confirm the slop described still exists. If a finding no",
        "  longer applies (already cleaned up), skip just that finding. If NONE apply, no-op.",
        "",
        "## STEP 1 — Branch off freshly-fetched origin/main",
        "- `git fetch origin` then branch from `origin/main` (the desktop checkout drifts).",
        f"- Use branch `{branch}`.",
        "",
        "## STEP 2 — Re-judge each finding, then apply only the real ones",
        "- The advisory finding is a SUGGESTION, not an order. Before deleting ANY comment,",
        "  re-apply the `technical-deslop` KEEP list"
        " (`.claude/skills/technical-deslop/references/slop-patterns.md`).",
        "- If the targeted comment explains WHY / WHEN / under WHICH deployment-or-runtime mode",
        "  something is done, or is a dated decision/migration note (`# YYYY-MM-DD — …`), it is",
        "  rationale, NOT slop: treat the finding as a FALSE POSITIVE — skip it and record why.",
        "  When in doubt, KEEP the comment.",
        "- Apply only the findings that survive re-judgement, and make exactly that cleanup",
        "  (e.g. delete a truly-redundant `# increment i` over `i += 1`, tighten a message).",
        "  Behavior must be byte-for-byte identical at runtime.",
        "- Do NOT refactor unrelated code, rename public symbols, change control flow, or",
        "  touch any file not named in a surviving finding. If a fix would require that, STOP",
        "  and comment on the issue explaining why, then exit.",
        "",
        "## STEP 3 — Super-verify locally (the no-broken-fix bar)",
        "- Run the FULL PR gate locally and confirm green:",
        "    `uv run ruff check --select E9,F .`",
        "    `uv run python -m py_compile <each changed .py>`",
        "    `uv run pytest tests/unit -x -q`",
        "- If the change is behavior-touching (it should NOT be for a deslop fix), add a",
        "  focused red-green regression test. For pure comment/message cleanup, confirm the",
        "  existing suite stays green.",
        "",
        "## STEP 4 — Open the PR",
        f"- Commit and push branch `{branch}`.",
        f"- {pr_posture}",
        "- In the PR body, include a **per-finding ledger** so a reviewer sees your judgement:",
        "  for EACH finding list `APPLIED` or `KEPT (false positive)`, the file, and the exact",
        "  comment text you removed or chose to keep — with a one-line reason for each KEEP.",
        f"- Also write `Closes #{issue.number}` so merging closes the issue.",
        "",
        "## Hard constraints (NEVER violate)",
        "- Do NOT merge, do NOT push to main, do NOT deploy, do NOT touch secrets/Infisical,",
        "  DB schema/migrations, *.db files, systemd units, scripts/deploy/**, or",
        "  .github/workflows/**. If a finding seems to point at one of these, STOP and",
        "  comment — triage should have caught it; treat its presence as a red flag.",
        "- Do NOT reintroduce `actions/checkout` to any GitHub Actions job.",
        "",
        "## Work product",
        f"- A {'DRAFT ' if draft else ''}PR linked to issue #{issue.number} containing only the"
        " behavior-preserving cleanup, with the full local gate green — OR a clear no-op"
        " explanation in COMPLETION.md if STEP 0 short-circuited.",
    ]
    return "\n".join(lines)


def _resolve_codebase_root() -> str:
    explicit = (os.getenv("UA_PROACTIVE_CODIE_CODEBASE_ROOT") or "").strip()
    if explicit:
        return explicit
    try:
        from universal_agent.codebase_policy import (
            DEFAULT_APPROVED_CODEBASE_ROOT,
            approved_codebase_roots_from_env,
        )

        approved = approved_codebase_roots_from_env()
        if approved:
            return approved[0]
        return DEFAULT_APPROVED_CODEBASE_ROOT
    except Exception:
        return "/opt/universal_agent"


def _cody_task_id(issue: DeslopIssue) -> str:
    return f"deslop-autofix:issue{issue.number}"


def build_email(issue: DeslopIssue, delivery: str, *, mode: str) -> tuple[str, str]:
    """The 'check this fix' email to Kevin. Returns ``(subject, text)``."""
    why_draft = (
        "It touches a file on the HARD never-auto list (deploy/secrets/schema/CI/etc.),"
        " so it is draft-only no matter what."
        if delivery == DELIVERY_DRAFT_EMAIL and classify_issue(issue)[0] == CLASS_NEVER_AUTO
        else f"Auto-remediation is in `{mode}` mode (draft + review before any merge)."
    )
    files = ", ".join(issue.files) or "(see issue)"
    subject = f"[deslop auto-remediate] draft fix dispatched for issue #{issue.number}"
    text = (
        f"A Cody fix mission was dispatched for deslop issue #{issue.number}.\n\n"
        f"Title: {issue.title}\n"
        f"Files: {files}\n"
        f"Delivery: DRAFT PR + this email.\n"
        f"Why draft: {why_draft}\n\n"
        f"What to do: a DRAFT PR will appear shortly. Review it, and if the fix is good,"
        f" mark it Ready for review to let it merge. If it looks wrong, close it.\n\n"
        f"Issue: https://github.com/{DEFAULT_REPO}/issues/{issue.number}\n"
    )
    return subject, text


# --------------------------------------------------------------------------- #
# Action executors (impure)
# --------------------------------------------------------------------------- #


def dispatch_cody_fix(
    issue: DeslopIssue,
    delivery: str,
    *,
    repo: str = DEFAULT_REPO,
    mode: str = DEFAULT_MODE,
    gh: GhRunner = _real_gh,
    db_path: Optional[str] = None,
    emailer: Optional[Callable[..., dict]] = None,
) -> dict[str, Any]:
    """Claim the issue (``deslop-dispatched``), queue the Cody mission, email Kevin.
    Claim-before-enqueue is the dedup guard against a double timer fire."""
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.services.proactive_codie import CODIE_TARGET_AGENT
    from universal_agent.services.proactive_task_builder import queue_proactive_task

    claimed = _add_label(repo, issue.number, LABEL_DISPATCHED, gh)

    root = _resolve_codebase_root()
    task_id = _cody_task_id(issue)
    brief = build_cody_brief(issue, delivery, repo=repo)

    conn = connect_runtime_db(db_path or get_activity_db_path())
    try:
        item = queue_proactive_task(
            conn,
            task_id=task_id,
            source_kind="deslop_autoremediate",
            source_ref=f"issue-{issue.number}",
            title=f"Deslop auto-fix: issue #{issue.number}",
            description=brief,
            priority=3,
            labels=[task_hub.TASK_LABEL_AGENT_READY, "proactive-codie", "deslop-autofix", "code"],
            metadata={
                "source": "deslop_autoremediate",
                "issue_number": issue.number,
                "source_pr": issue.source_pr,
                "files": issue.files,
                "delivery": delivery,
                "mode": mode,
                "review_gate": (
                    "draft_pr_human_review"
                    if delivery == DELIVERY_DRAFT_EMAIL
                    else "non_draft_then_auto_merge"
                ),
                "complexity_target": "low",
                "target_agent": CODIE_TARGET_AGENT,
                "codebase_root": root,
                "external_effect_policy": {
                    "allow_pr": True,
                    "allow_merge": False,
                    "allow_main_push": False,
                    "allow_deploy": False,
                    "allow_payments": False,
                    "allow_public_communications": False,
                    "allow_destructive_ops": False,
                    "allow_secret_mutation": False,
                    "allow_major_dep_bump": False,
                    "allow_control_plane_edits": False,
                },
                "workflow_manifest": {
                    "workflow_kind": "code_change",
                    "delivery_mode": "interactive_chat",
                    "final_channel": "chat",
                    "canonical_executor": "simone_first",
                    "target_agent": CODIE_TARGET_AGENT,
                    "codebase_root": root,
                    "repo_mutation_allowed": True,
                },
            },
        )
        conn.commit()
    finally:
        conn.close()

    # Notify Kevin a draft PR is coming — gated by UA_DESLOP_NOTIFY_OPERATOR
    # (default off). When quiet, the durable records above (claim label + Task Hub
    # row) are the only trace; nothing reaches the operator inbox.
    email_result: dict[str, Any] = {"status": "skipped"}
    if not _notify_operator():
        email_result = {"status": "suppressed", "reason": "UA_DESLOP_NOTIFY_OPERATOR=0"}
    else:
        subject, text = build_email(issue, delivery, mode=mode)
        send = emailer
        if send is None:
            try:
                from universal_agent.simone_mail import (
                    send_simone_email as send,  # type: ignore
                )
            except Exception as exc:  # pragma: no cover - best effort
                email_result = {"status": "failed", "reason": f"import:{exc}"}
                send = None
        if send is not None:
            try:
                email_result = send(subject=subject, text=text, source="deslop-autoremediate")
            except Exception as exc:  # pragma: no cover - best effort
                email_result = {"status": "failed", "reason": str(exc)}

    nudge = "skipped"
    try:
        from universal_agent.services.idle_dispatch_loop import nudge_dispatch

        nudge_dispatch(reason=f"deslop_autofix_dispatched:{task_id}")
        nudge = "requested"
    except Exception as exc:  # pragma: no cover - best effort
        nudge = f"failed:{type(exc).__name__}"

    return {
        "action": "dispatch",
        "delivery": delivery,
        "claimed_label": claimed,
        "task_id": task_id,
        "task": item,
        "email": email_result,
        "dispatch_nudge": nudge,
    }


def escalate_to_operator(
    issue: DeslopIssue,
    reason: str,
    *,
    repo: str = DEFAULT_REPO,
    gh: GhRunner = _real_gh,
    telegram: Optional[Callable[..., tuple[bool, str]]] = None,
) -> dict[str, Any]:
    """Label ``needs-operator`` and Telegram-ping. For findings that aren't a
    confident, behavior-preserving code fix."""
    labelled = _add_label(repo, issue.number, LABEL_NEEDS_OPERATOR, gh)

    chat_id = (
        os.getenv("UA_OPERATOR_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or ""
    ).strip()
    bot_token = (
        os.getenv("UA_OPERATOR_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    ).strip() or None

    msg = (
        f"🧹 Deslop finding needs you — issue #{issue.number}\n"
        f"{issue.title}\n"
        f"files={', '.join(issue.files) or '(unparseable)'}\n"
        f"Not confidently auto-fixable ({reason}).\n"
        f"https://github.com/{repo}/issues/{issue.number}"
    )

    sent, detail = (False, "no_chat_id")
    if not _notify_operator():
        # Quiet by default — the needs-operator label + Task Hub record above are
        # the durable trace an agent reads; nothing pings the operator.
        sent, detail = False, "suppressed:UA_DESLOP_NOTIFY_OPERATOR=0"
    else:
        send = telegram
        if send is None and chat_id:
            try:
                from universal_agent.services.telegram_send import (
                    telegram_send_sync as send,  # type: ignore
                )
            except Exception as exc:  # pragma: no cover - best effort
                send = None
                detail = f"import:{exc}"
        if send is not None and chat_id:
            try:
                sent, detail = send(chat_id=chat_id, text=msg, bot_token=bot_token)
            except Exception as exc:  # pragma: no cover - best effort
                sent, detail = False, f"{type(exc).__name__}:{exc}"

    return {
        "action": "escalate",
        "reason": reason,
        "labelled_needs_operator": labelled,
        "telegram_sent": sent,
        "telegram_detail": detail,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def process_issue(
    issue: DeslopIssue,
    *,
    mode: str,
    repo: str = DEFAULT_REPO,
    gh: GhRunner = _real_gh,
    db_path: Optional[str] = None,
    dry_run: bool = False,
    root: Optional[str] = None,
    emailer: Optional[Callable[..., dict]] = None,
    telegram: Optional[Callable[..., tuple[bool, str]]] = None,
) -> dict[str, Any]:
    action, delivery, reason = decide_action(issue, mode, root=root)

    # Source-PR-state gate: a deslop finding's slop only lives on `main` once its
    # source PR merged. Re-route a would-be dispatch when the PR is still open
    # (comment there instead) or was closed unmerged (close the issue as moot).
    source_pr_disposition: Optional[str] = None
    sp_reason = ""
    if action == "dispatch" and issue.source_pr is not None and _check_source_pr_enabled():
        pr_state = fetch_pr_state(repo, issue.source_pr, gh)
        source_pr_disposition, sp_reason = decide_source_pr_disposition(pr_state)
        if source_pr_disposition != SRC_PR_PROCEED:
            reason = f"{reason}|{sp_reason}"

    result: dict[str, Any] = {
        "issue_number": issue.number,
        "title": issue.title,
        "files": issue.files,
        "mode": mode,
        "action": action,
        "delivery": delivery,
        "reason": reason,
        "source_pr": issue.source_pr,
        "source_pr_disposition": source_pr_disposition,
    }
    if dry_run:
        result["dry_run"] = True
        if action == "dispatch" and source_pr_disposition in (None, SRC_PR_PROCEED):
            result["would_email_subject"] = build_email(issue, delivery, mode=mode)[0]
            result["brief_preview"] = build_cody_brief(issue, delivery, repo=repo)[:600]
        elif source_pr_disposition == SRC_PR_DEFER:
            result["would_comment_on_pr"] = issue.source_pr
        elif source_pr_disposition == SRC_PR_CLOSE_MOOT:
            result["would_close_issue"] = True
        return result

    if action == "skip":
        return result
    if action == "dispatch":
        if source_pr_disposition == SRC_PR_DEFER:
            result.update(defer_to_open_source_pr(issue, repo=repo, gh=gh))
            return result
        if source_pr_disposition == SRC_PR_CLOSE_MOOT:
            result.update(close_moot_issue(issue, sp_reason, repo=repo, gh=gh))
            return result
        result.update(
            dispatch_cody_fix(
                issue,
                delivery or DELIVERY_DRAFT_EMAIL,
                repo=repo,
                mode=mode,
                gh=gh,
                db_path=db_path,
                emailer=emailer,
            )
        )
        return result
    if action == "escalate":
        result.update(escalate_to_operator(issue, reason, repo=repo, gh=gh, telegram=telegram))
        return result
    return result


def run_dispatch(
    *,
    mode: str = DEFAULT_MODE,
    repo: str = DEFAULT_REPO,
    gh: GhRunner = _real_gh,
    db_path: Optional[str] = None,
    limit: int = 50,
    dry_run: bool = False,
    root: Optional[str] = None,
    emailer: Optional[Callable[..., dict]] = None,
    telegram: Optional[Callable[..., tuple[bool, str]]] = None,
) -> dict[str, Any]:
    """Poll open ``deslop-findings`` issues, triage + act on each. Returns a
    JSON-serializable summary. ``root`` (codebase root, for the DDL content scan)
    defaults to the approved codebase root; absent on disk -> path gate only."""
    if root is None:
        root = _resolve_codebase_root()
    numbers = list_open_finding_issue_numbers(repo, gh, limit=limit)
    processed: list[dict[str, Any]] = []
    for number in numbers:
        issue = fetch_issue(repo, number, gh)
        if issue is None:
            processed.append({"issue_number": number, "action": "skip", "reason": "fetch_failed"})
            continue
        processed.append(
            process_issue(
                issue,
                mode=mode,
                repo=repo,
                gh=gh,
                db_path=db_path,
                dry_run=dry_run,
                root=root,
                emailer=emailer,
                telegram=telegram,
            )
        )
    return {
        "ok": True,
        "mode": mode,
        "dry_run": dry_run,
        "repo": repo,
        "root": root,
        "open_issue_count": len(numbers),
        "processed": processed,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        default=os.getenv("UA_DESLOP_AUTOREMEDIATE_MODE", DEFAULT_MODE),
        choices=[MODE_OBSERVE, MODE_AUTO],
        help="observe (default): always draft + email. auto: safe class auto-merges.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--db-path", default="", help="Override activity DB path.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Triage + print decisions only — no label, no dispatch, no email.",
    )
    args = parser.parse_args(argv)

    # Cron/timer subprocesses inherit Infisical-loaded env, but bootstrap
    # defensively in case this is run standalone.
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets()
    except Exception:
        pass

    try:
        result = run_dispatch(
            mode=args.mode,
            repo=args.repo,
            db_path=args.db_path or None,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}"}, indent=2))
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
