from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SdkRuntimeInfo:
    sdk_version: str
    bundled_cli_version: str


def _safe_version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in str(version or "").strip().split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if digits:
            parts.append(int(digits))
        else:
            parts.append(0)
    return tuple(parts)


def read_sdk_runtime_info() -> SdkRuntimeInfo:
    sdk_version = "unknown"
    bundled_cli_version = "unknown"
    try:
        import claude_agent_sdk  # type: ignore

        sdk_version = str(getattr(claude_agent_sdk, "__version__", "unknown") or "unknown")
        try:
            from claude_agent_sdk import _cli_version  # type: ignore

            bundled_cli_version = str(
                getattr(_cli_version, "__cli_version__", "unknown") or "unknown"
            )
        except Exception:
            bundled_cli_version = "unknown"
    except Exception:
        pass
    return SdkRuntimeInfo(
        sdk_version=sdk_version,
        bundled_cli_version=bundled_cli_version,
    )


def sdk_version_is_at_least(required: str, *, current: Optional[str] = None) -> bool:
    use_current = str(current or "").strip()
    if not use_current:
        use_current = read_sdk_runtime_info().sdk_version
    if use_current == "unknown":
        return False
    return _safe_version_tuple(use_current) >= _safe_version_tuple(required)


def emit_sdk_runtime_banner(required: str = "0.1.48") -> SdkRuntimeInfo:
    info = read_sdk_runtime_info()
    _LOGGER.info(
        "Claude Agent SDK runtime versions: sdk=%s bundled_cli=%s",
        info.sdk_version,
        info.bundled_cli_version,
    )
    if not sdk_version_is_at_least(required, current=info.sdk_version):
        _LOGGER.warning(
            "Claude Agent SDK version %s is below the required minimum %s",
            info.sdk_version,
            required,
        )
    return info
