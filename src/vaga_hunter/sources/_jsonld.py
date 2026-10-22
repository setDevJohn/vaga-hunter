"""Helper: extrai o bloco JSON-LD JobPosting de uma página de vaga."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup


def _is_jobposting(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    type_value = item.get("@type")
    if isinstance(type_value, list):
        return "JobPosting" in type_value
    return type_value == "JobPosting"


def extract_jobposting(html: str) -> dict:
    """Retorna o dict do JobPosting (schema.org) ou {}."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        # alguns sites embrulham em @graph
        expanded = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                expanded.extend(item["@graph"])
            expanded.append(item)
        for item in expanded:
            if _is_jobposting(item):
                return item
    return {}


def jobposting_description(html: str) -> str:
    """Descrição em texto puro do JobPosting; '' se ausente."""
    description = extract_jobposting(html).get("description", "")
    if not description:
        return ""
    return BeautifulSoup(str(description), "lxml").get_text(" ", strip=True)
