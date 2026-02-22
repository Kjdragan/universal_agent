"""CSI logging setup."""

from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level_name = (os.getenv("CSI_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

