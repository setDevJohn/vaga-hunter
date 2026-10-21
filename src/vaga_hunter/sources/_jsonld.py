"""Helper: extrai o bloco JSON-LD JobPosting de uma página de vaga."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup


def extract_jobposting(html: str) -> dict:
    """Retorna o dict do JobPosting (schema.org) ou {}."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}


def jobposting_description(html: str) -> str:
    """Descrição em texto puro do JobPosting; '' se ausente."""
    description = extract_jobposting(html).get("description", "")
    if not description:
        return ""
    return BeautifulSoup(description, "lxml").get_text(" ", strip=True)
