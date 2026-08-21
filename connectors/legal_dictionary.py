"""Legal dictionary / glossary connector (dummy)."""

import logging
from typing import Any
from connectors.base import ConnectorStatus, register_connector

logger = logging.getLogger("legal_assist.connectors.legal_dictionary")
NAME = "legal_dictionary"


class LegalDictionaryConnector:
    """Look up legal terms and definitions.

    Dummy implementation with a small built-in glossary.
    """

    _GLOSSARY: dict[str, str] = {
        "habeas corpus": "A writ requiring a person under arrest to be brought before a judge.",
        "mens rea": "The intention or knowledge of wrongdoing (guilty mind).",
        "actus reus": "The action or conduct that constitutes a criminal offence.",
        "prima facie": "Based on first impression; accepted as correct until proved otherwise.",
        "caveat": "A warning or proviso of specific stipulations, conditions, or limitations.",
        "sub judice": "Under judicial consideration; not yet decided by a court.",
        "force majeure": "Unforeseeable circumstances preventing fulfilment of a contract.",
        "ultra vires": "Beyond one's legal power or authority.",
        "res judicata": "A matter already judged by a competent court; cannot be pursued further.",
        "amicus curiae": "A person who is not a party to a case but offers information to assist the court.",
    }

    def status(self) -> dict[str, Any]:
        return ConnectorStatus.available(NAME, {"terms": len(self._GLOSSARY)})

    def define(self, term: str) -> dict[str, Any]:
        key = term.strip().lower()
        definition = self._GLOSSARY.get(key)
        if definition:
            return {"connector": NAME, "available": True, "term": term, "definition": definition}
        # fuzzy match
        matches = [k for k in self._GLOSSARY if key in k]
        if matches:
            return {
                "connector": NAME,
                "available": True,
                "term": term,
                "definition": None,
                "suggestions": [{"term": m, "definition": self._GLOSSARY[m]} for m in matches[:5]],
            }
        return {"connector": NAME, "available": True, "term": term, "definition": None, "suggestions": []}

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        logger.debug("search(term=%r)", query)
        key = query.strip().lower()
        matches = [(k, v) for k, v in self._GLOSSARY.items() if key in k or key in v.lower()]
        return {
            "connector": NAME,
            "available": True,
            "query": query,
            "results": [{"term": k, "definition": v} for k, v in matches[:limit]],
            "total": len(matches),
        }


register_connector(NAME, LegalDictionaryConnector())
