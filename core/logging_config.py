"""Central logging configuration for the whole application.

Every module obtains its logger via ``logging.getLogger("legal_assist.<area>")``;
this module configures the root format/level exactly once at startup
(called from main.py).  Level is overridable with the LOG_LEVEL env var.
"""

import logging
import os

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str | None = None) -> None:
    """Configure root logging once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    resolved = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(level=getattr(logging, resolved, logging.INFO), format=LOG_FORMAT)
    # Third-party noise reduction
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True
    logging.getLogger("legal_assist.core").info("Logging configured (level=%s)", resolved)
