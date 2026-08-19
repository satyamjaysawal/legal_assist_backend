"""Connector stubs for legal data sources.

Every connector follows the same interface:
  - ``status()`` → dict with ok/available info
  - ``search(query, **kwargs)`` → list of results
  - ``get(id)`` → single record or None

All connectors currently return ``{"available": False}`` and dummy data.
When a real API / knowledge-base becomes available, just replace the
stub body — the interface stays the same.
"""

from connectors.base import ConnectorStatus, connector_registry, list_connectors
from connectors.indian_kanoon import IndianKanoonConnector
from connectors.bare_acts import BareActsConnector
from connectors.court_api import CourtAPIConnector
from connectors.legal_templates import LegalTemplatesConnector
from connectors.legal_dictionary import LegalDictionaryConnector
from connectors.neon_postgres import NeonPostgresConnector

__all__ = [
    "ConnectorStatus",
    "connector_registry",
    "list_connectors",
    "IndianKanoonConnector",
    "BareActsConnector",
    "CourtAPIConnector",
    "LegalTemplatesConnector",
    "LegalDictionaryConnector",
    "NeonPostgresConnector",
]
