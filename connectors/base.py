"""Base connector interface and registry."""

import logging
from typing import Any

logger = logging.getLogger("legal_assist.connectors")


class ConnectorStatus:
    """Mixin / helper for connector health reporting."""

    @staticmethod
    def unavailable(name: str) -> dict[str, Any]:
        return {"name": name, "available": False, "reason": "Connector not yet configured"}

    @staticmethod
    def available(name: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        base = {"name": name, "available": True}
        if extra:
            base.update(extra)
        return base


# ── Global connector registry ───────────────────────────────────
connector_registry: dict[str, Any] = {}


def register_connector(name: str, instance: Any) -> None:
    connector_registry[name] = instance
    logger.debug("Connector registered: %s", name)


def get_connector(name: str) -> Any | None:
    return connector_registry.get(name)


def list_connectors() -> list[dict[str, Any]]:
    results = []
    for name, inst in connector_registry.items():
        try:
            results.append(inst.status())
        except Exception as exc:
            logger.warning("Connector '%s' status check failed: %s", name, exc)
            results.append({"name": name, "available": False, "error": str(exc)})
    return results
