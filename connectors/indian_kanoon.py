"""Indian Kanoon connector — case law search (dummy)."""

from typing import Any
from connectors.base import ConnectorStatus, register_connector

NAME = "indian_kanoon"


class IndianKanoonConnector:
    """Search Indian case law (indiankanoon.org).

    Currently returns dummy data.  Replace ``search`` and ``get_judgment``
    with real API calls when the connector is provisioned.
    """

    def status(self) -> dict[str, Any]:
        return ConnectorStatus.unavailable(NAME)

    def search(self, query: str, court: str = "all", year_from: int | None = None, limit: int = 5) -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": False,
            "query": query,
            "results": [
                {
                    "title": "[Dummy] State vs Example — IPC §420",
                    "citation": "AIR 2020 SC 1234",
                    "court": "Supreme Court",
                    "year": 2020,
                    "snippet": "This is a dummy result. Connect the real Indian Kanoon API to get actual case law.",
                },
            ],
            "total": 1,
            "note": "Dummy data — real API not configured",
        }

    def get_judgment(self, citation: str) -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": False,
            "citation": citation,
            "text": "Full judgment text would appear here when the API is connected.",
            "note": "Dummy data",
        }


# auto-register on import
register_connector(NAME, IndianKanoonConnector())
