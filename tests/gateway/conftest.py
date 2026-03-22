"""Gateway test configuration — bootstrap Infisical secrets.

Ensures API keys (COMPOSIO_API_KEY, etc.) are available in the test
environment by calling the same Infisical bootstrap used at runtime.
"""

import logging
import os

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_infisical_for_gateway_tests():
    """Load Infisical secrets into os.environ for the entire test session.

    Uses the same ``initialize_runtime_secrets`` path that the production
    gateway relies on.  Falls back silently so that purely-mocked tests
    still run when Infisical credentials are not configured.
    """
    if os.getenv("COMPOSIO_API_KEY"):
        # Already present (e.g. CI env, manual export) — nothing to do.
        return

    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        result = initialize_runtime_secrets(profile="local_workstation", force_reload=True)
        if result.ok:
            logger.info(
                "Gateway test conftest: Infisical bootstrap loaded %d secrets (source=%s)",
                result.loaded_count,
                result.source,
            )
        else:
            logger.warning(
                "Gateway test conftest: Infisical bootstrap NOT ok — some tests may fail (%s)",
                result.errors,
            )
    except Exception as exc:
        logger.warning(
            "Gateway test conftest: Infisical bootstrap failed — %s. "
            "Tests requiring API keys (COMPOSIO_API_KEY) may fail.",
            exc,
        )
