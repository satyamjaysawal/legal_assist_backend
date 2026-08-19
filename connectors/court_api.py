"""eCourts / court system connector (dummy)."""

from typing import Any
from connectors.base import ConnectorStatus, register_connector

NAME = "court_api"


class CourtAPIConnector:
    """Interface to eCourts / NJDG for case status and hearing dates.

    Dummy implementation.
    """

    def status(self) -> dict[str, Any]:
        return ConnectorStatus.unavailable(NAME)

    def check_case_status(self, case_number: str, court: str = "") -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": False,
            "case_number": case_number,
            "status": "unknown",
            "next_hearing": None,
            "note": "Dummy — connect eCourts API for real data",
        }

    def get_hearing_dates(self, case_number: str) -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": False,
            "case_number": case_number,
            "hearings": [],
            "note": "Dummy data",
        }

    def find_court(self, jurisdiction: str, case_type: str = "") -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": False,
            "jurisdiction": jurisdiction,
            "courts": [
                {"name": f"[Dummy] District Court — {jurisdiction}", "type": "district"},
                {"name": f"[Dummy] High Court — {jurisdiction}", "type": "high_court"},
            ],
            "note": "Dummy data",
        }


register_connector(NAME, CourtAPIConnector())
