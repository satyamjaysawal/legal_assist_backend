"""Bare Acts / India Code connector (dummy)."""

import logging
from typing import Any
from connectors.base import ConnectorStatus, register_connector

logger = logging.getLogger("legal_assist.connectors.bare_acts")
NAME = "bare_acts"


class BareActsConnector:
    """Search Indian Bare Acts and statutes (indiacode.nic.in).

    Dummy implementation — returns placeholder data.
    """

    def status(self) -> dict[str, Any]:
        return ConnectorStatus.unavailable(NAME)

    def search(self, act_name: str, section: str = "", limit: int = 5) -> dict[str, Any]:
        logger.debug("search(act=%r, section=%r) — dummy results", act_name, section)
        return {
            "connector": NAME,
            "available": False,
            "query": f"{act_name} §{section}" if section else act_name,
            "results": [
                {
                    "act": act_name or "Indian Penal Code",
                    "section": section or "420",
                    "title": f"[Dummy] Section {section or '420'} of {act_name or 'IPC'}",
                    "text": "Dummy bare act text. Connect the India Code API for real content.",
                },
            ],
            "total": 1,
            "note": "Dummy data — real API not configured",
        }

    def get_section(self, act_name: str, section_number: str) -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": False,
            "act": act_name,
            "section": section_number,
            "text": "Full section text would appear here.",
            "amendments": [],
            "note": "Dummy data",
        }

    def get_amendment_history(self, act_name: str) -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": False,
            "act": act_name,
            "amendments": [],
            "note": "Dummy data",
        }


register_connector(NAME, BareActsConnector())
