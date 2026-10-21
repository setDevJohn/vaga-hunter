from datetime import datetime, timedelta, timezone

import pytest

from vaga_hunter.config import ProfileConfig, ScoringConfig
from vaga_hunter.models import Job
from vaga_hunter.scoring import Scorer

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


@pytest.fixture
def scorer() -> Scorer:
    profile = ProfileConfig(
        roles=["fullstack", "frontend", "backend", "react native",
               "devops", "cloud", "platform"],
        seniority=["junior", "pleno"],
        exclude_keywords_hard=["estágio", "estagiário", "trainee"],
        exclude_keywords_soft=["senior", "sênior", "sr", "especialista"],
        include_keywords=["react", "typescript", "node", "python"],
    )
    return Scorer(profile, ScoringConfig())


def make_job(title: str, description: str = "", days_ago: float = 0.5,
             remote: bool | None = None) -> Job:
    return Job(source="test", external_id="1", title=title,
               description=description,
               posted_at=NOW - timedelta(days=days_ago), remote=remote)


def test_fullstack_pleno_remote_scores_high(scorer):
    job = make_job("Desenvolvedor Fullstack Pleno",
                   "Vaga remota. Stack: React, Node, TypeScript.", remote=True)
    score, details = scorer.score(job, now=NOW)
    assert score >= 80
    assert details["role_match"] == 30


def test_senior_penalized(scorer):
    junior = make_job("Desenvolvedor Backend Junior Python")
    senior = make_job("Senior Backend Developer Python")
    s_junior, _ = scorer.score(junior, now=NOW)
    s_senior, details = scorer.score(senior, now=NOW)
    assert s_senior < s_junior
    assert details["seniority"] == 0


def test_estagio_hard_excluded(scorer):
    job = make_job("Estágio em Desenvolvimento Fullstack React")
    score, details = scorer.score(job, now=NOW)
    assert score == 0
    assert "hard_exclude" in details


def test_mobile_only_react_native(scorer):
    flutter = make_job("Desenvolvedor Mobile Flutter Pleno")
    rn = make_job("React Native Developer Pleno")
    s_flutter, _ = scorer.score(flutter, now=NOW)
    s_rn, _ = scorer.score(rn, now=NOW)
    assert s_rn > s_flutter
    assert s_rn >= 60


def test_devops_only_junior(scorer):
    junior = make_job("DevOps Engineer Junior")
    pleno = make_job("DevOps Engineer Pleno")
    s_junior, _ = scorer.score(junior, now=NOW)
    s_pleno, _ = scorer.score(pleno, now=NOW)
    assert s_junior > s_pleno


def test_recency_decay(scorer):
    fresh = make_job("Desenvolvedor Fullstack Pleno", days_ago=0.5)
    old = make_job("Desenvolvedor Fullstack Pleno", days_ago=10)
    s_fresh, d_fresh = scorer.score(fresh, now=NOW)
    s_old, d_old = scorer.score(old, now=NOW)
    assert s_fresh > s_old
    assert d_fresh["recency"] > d_old["recency"]


def test_accent_insensitive(scorer):
    job1 = make_job("Desenvolvedor Backend Júnior")
    job2 = make_job("Desenvolvedor Backend Junior")
    s1, _ = scorer.score(job1, now=NOW)
    s2, _ = scorer.score(job2, now=NOW)
    assert s1 == s2


def test_sr_word_boundary(scorer):
    # "sr" como palavra penaliza; "sra" ou dentro de palavra não
    senior = make_job("Desenvolvedor Backend Sr")
    normal = make_job("Desenvolvedor Backend para SRE squad")
    s_senior, _ = scorer.score(senior, now=NOW)
    s_normal, _ = scorer.score(normal, now=NOW)
    assert s_senior < s_normal
