"""Scoring transparente por regras: 0-100 com breakdown por componente."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from .config import ProfileConfig, ScoringConfig
from .models import Job

# Sinônimos PT/EN normalizados
ROLE_PATTERNS: dict[str, list[str]] = {
    "fullstack": ["fullstack", "full stack", "full-stack"],
    "frontend": ["frontend", "front end", "front-end"],
    "backend": ["backend", "back end", "back-end"],
    "react native": ["react native"],
    "devops": ["devops", "dev ops"],
    "cloud": ["cloud engineer", "engenheiro de cloud", "engenheira de cloud"],
    "platform": ["platform engineer", "engenheiro de plataforma",
                 "engenheira de plataforma"],
    "developer": ["desenvolvedor", "desenvolvedora", "developer", "programador",
                  "programadora", "engenheiro de software", "software engineer"],
}
JUNIOR_WORDS = ["junior", "júnior", "jr"]
PLENO_WORDS = ["pleno", "mid-level", "mid level", "intermediário"]
SENIOR_WORDS = ["senior", "sênior", "sr", "especialista", "lead", "principal", "staff"]
MOBILE_WORDS = ["mobile", "android", "ios", "flutter", "kotlin", "swift"]
INFRA_ROLES = ("devops", "cloud", "platform")
REMOTE_WORDS = ["remoto", "remote", "home office", "homeoffice", "anywhere"]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _contains(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(_normalize(term))}(?![a-z0-9])", text) is not None


class Scorer:
    def __init__(self, profile: ProfileConfig, scoring: ScoringConfig):
        self.profile = profile
        self.weights = scoring.weights

    def score(self, job: Job, now: datetime | None = None) -> tuple[float, dict[str, float]]:
        """Retorna (score 0-100, breakdown). Hard-exclude zera tudo."""
        title = _normalize(job.title)
        full = _normalize(f"{job.title}\n{job.description}")
        details: dict[str, float] = {}

        # Hard exclude (estágio, trainee...) — só no título para não zerar por
        # menção casual na descrição.
        for term in self.profile.exclude_keywords_hard:
            if _contains(title, term):
                return 0.0, {"hard_exclude": 0.0, "matched": 1.0}

        details["role_match"] = self._role_score(title) * self.weights.get("role_match", 30)
        details["seniority"] = self._seniority_score(title, full) * self.weights.get("seniority", 20)
        details["keywords"] = self._keywords_score(title, full) * self.weights.get("keywords", 20)
        details["remote"] = self._remote_score(job, title, full) * self.weights.get("remote", 15)
        details["recency"] = self._recency_score(job, now) * self.weights.get("recency", 15)

        total = max(0.0, min(100.0, sum(details.values())))
        return round(total, 1), {k: round(v, 1) for k, v in details.items()}

    # --- componentes (cada um retorna fator 0..1, exceto penalidades) -----

    def _role_score(self, title: str) -> float:
        matched: set[str] = set()
        for role, patterns in ROLE_PATTERNS.items():
            if any(_contains(title, p) for p in patterns):
                matched.add(role)

        wanted = {_normalize(r) for r in self.profile.roles}
        hits = {r for r in matched if _normalize(r) in wanted}

        # Mobile só pontua com React Native
        if any(_contains(title, w) for w in MOBILE_WORDS) and "react native" not in matched:
            return 0.1 if hits else 0.0

        if hits - {"developer"}:
            return 1.0
        if "developer" in matched:  # título genérico de dev
            return 0.6
        return 0.0

    def _seniority_score(self, title: str, full: str) -> float:
        is_junior = any(_contains(title, w) for w in JUNIOR_WORDS)
        is_pleno = any(_contains(title, w) for w in PLENO_WORDS)
        is_senior = any(_contains(title, w) for w in SENIOR_WORDS)

        # devops/cloud/platform: usuário só aceita junior
        infra = any(
            any(_contains(title, p) for p in ROLE_PATTERNS[r]) for r in INFRA_ROLES
        )
        if infra and not is_junior:
            return 0.2

        if is_senior:
            return 0.0
        # Penalidade soft por termos configurados (ex.: "especialista") no título
        for term in self.profile.exclude_keywords_soft:
            if _contains(title, term):
                return 0.0

        if is_junior or is_pleno:
            return 1.0
        # Sem senioridade no título: neutro-positivo (muitas vagas não marcam)
        return 0.6

    def _keywords_score(self, title: str, full: str) -> float:
        include = self.profile.include_keywords
        if not include:
            return 0.5
        points = 0.0
        for kw in include:
            if _contains(title, kw):
                points += 2.0
            elif _contains(full, kw):
                points += 1.0
        # Satura: 4 pontos (ex.: 2 keywords no título) = fator máximo
        factor = min(1.0, points / 4.0)
        for term in self.profile.exclude_keywords_soft:
            if _contains(full, term) and not _contains(title, term):
                factor = max(0.0, factor - 0.1)
        return factor

    def _remote_score(self, job: Job, title: str, full: str) -> float:
        if job.remote is True:
            return 1.0
        if any(_contains(title, w) or _contains(full, w) for w in REMOTE_WORDS):
            return 1.0
        return 0.3  # desconhecido: neutro baixo

    def _recency_score(self, job: Job, now: datetime | None) -> float:
        if job.posted_at is None:
            return 0.5
        now = now or datetime.now(timezone.utc)
        posted = job.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        days = max(0.0, (now - posted).total_seconds() / 86400)
        if days <= 1:
            return 1.0
        if days <= 3:
            return 0.8
        if days <= 7:
            return 0.5
        if days <= 14:
            return 0.25
        return 0.1
