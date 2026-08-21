"""Legal document templates connector (dummy)."""

import logging
from typing import Any
from connectors.base import ConnectorStatus, register_connector

logger = logging.getLogger("legal_assist.connectors.legal_templates")
NAME = "legal_templates"


class LegalTemplatesConnector:
    """Provides legal document templates for common use cases.

    Dummy implementation — replace with a real template DB when available.
    """

    _DUMMY_TEMPLATES = {
        "legal_notice": {
            "name": "Legal Notice",
            "fields": ["sender_name", "recipient_name", "subject", "facts", "relief_sought", "date"],
            "template": (
                "LEGAL NOTICE\n\n"
                "Date: {date}\n\n"
                "To,\n{recipient_name}\n\n"
                "From,\n{sender_name}\n\n"
                "Subject: {subject}\n\n"
                "Sir/Madam,\n\n"
                "{facts}\n\n"
                "I hereby call upon you to {relief_sought} within 15 days "
                "of receipt of this notice, failing which I shall be constrained "
                "to initiate appropriate legal proceedings against you.\n\n"
                "Yours faithfully,\n{sender_name}"
            ),
        },
        "rent_agreement": {
            "name": "Rent Agreement",
            "fields": ["landlord", "tenant", "property", "rent_amount", "duration", "deposit"],
            "template": "RENT AGREEMENT\n\n(Dummy template — fill with real terms)",
        },
        "consumer_complaint": {
            "name": "Consumer Complaint",
            "fields": ["complainant", "opposite_party", "facts", "relief", "forum"],
            "template": "BEFORE THE {forum}\n\nComplaint No. ___ of 20__\n\n(Dummy template)",
        },
        "rti_application": {
            "name": "RTI Application",
            "fields": ["applicant", "public_authority", "information_sought", "date"],
            "template": "APPLICATION UNDER RIGHT TO INFORMATION ACT, 2005\n\n(Dummy template)",
        },
    }

    def status(self) -> dict[str, Any]:
        return ConnectorStatus.available(NAME, {"templates": len(self._DUMMY_TEMPLATES)})

    def list_templates(self) -> dict[str, Any]:
        return {
            "connector": NAME,
            "available": True,
            "templates": [
                {"id": k, "name": v["name"], "fields": v["fields"]}
                for k, v in self._DUMMY_TEMPLATES.items()
            ],
        }

    def get_template(self, template_id: str) -> dict[str, Any]:
        logger.debug("get_template(id=%r)", template_id)
        tpl = self._DUMMY_TEMPLATES.get(template_id)
        if not tpl:
            return {"connector": NAME, "available": False, "error": f"Template '{template_id}' not found"}
        return {"connector": NAME, "available": True, "template_id": template_id, **tpl}

    def fill_template(self, template_id: str, fields: dict[str, str]) -> dict[str, Any]:
        tpl = self._DUMMY_TEMPLATES.get(template_id)
        if not tpl:
            return {"connector": NAME, "available": False, "error": f"Template '{template_id}' not found"}
        try:
            filled = tpl["template"].format(**{k: fields.get(k, f"[{k}]") for k in tpl["fields"]})
        except KeyError:
            filled = tpl["template"]
        return {"connector": NAME, "available": True, "template_id": template_id, "document": filled}


register_connector(NAME, LegalTemplatesConnector())
