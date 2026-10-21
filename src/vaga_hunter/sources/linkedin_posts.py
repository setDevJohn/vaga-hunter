"""Fonte opcional: posts do LinkedIn com vagas, via SEU Chrome real (CDP).

⚠️ Usa a sua sessão logada. Risco de bloqueio é baixo (navegador real,
ritmo humano, pouquíssimas requisições) mas NÃO é zero. Desligada por padrão.

Como usar:
1. Pela UI: clique no pill "Posts" — ele abre o Chrome do bot sozinho.
   Pelo terminal (Chrome 136+ exige perfil próprio para a porta CDP):
     google-chrome --remote-debugging-port=9222 \
         --user-data-dir=~/.config/vaga-hunter-chrome
   No primeiro uso, faça login no LinkedIn nesse perfil (fica salvo).
2. No config.toml:
     [sources]
     linkedin_posts = true
     [sources.posts]
     mode = "browser"
3. Instale o extra:  pip install -e ".[posts]"
"""
from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse
from collections.abc import Iterator

from ..config import PostsConfig
from ..models import Job, SearchFilters
from .base import JobSource

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/search/results/content/?keywords={kw}&sortBy=%22date_posted%22"

# Padrões que indicam post de vaga (PT/EN)
JOB_PATTERNS = re.compile(
    r"vaga|estamos contratando|oportunidade de emprego|hiring|we're hiring|"
    r"job opening|open position|processo seletivo|envie seu curr[ií]culo|"
    r"candidate-se|inscreva-se",
    re.IGNORECASE,
)

# Executado na página para extrair os posts visíveis
EXTRACT_JS = """
() => {
  const posts = [];
  for (const el of document.querySelectorAll('div.feed-shared-update-v2, li.artdeco-card')) {
    const urn = el.getAttribute('data-urn')
      || el.querySelector('[data-urn]')?.getAttribute('data-urn') || '';
    const text = el.innerText || '';
    const author = el.querySelector(
      '.update-components-actor__title, .feed-shared-actor__title')?.innerText || '';
    if (text.trim()) posts.push({ urn, text: text.slice(0, 3000), author });
  }
  return posts;
}
"""


def extract_jobs_from_posts(posts: list[dict], query: str) -> list[Job]:
    """Filtra posts com cara de vaga e converte em Job. Função pura (testável)."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for post in posts:
        text = post.get("text", "")
        urn = post.get("urn", "")
        match = re.search(r"activity:(\d+)", urn)
        if not match or not JOB_PATTERNS.search(text):
            continue
        activity_id = match.group(1)
        if activity_id in seen:
            continue
        seen.add(activity_id)
        author = (post.get("author") or "").split("\n")[0].strip()
        first_line = next((line.strip() for line in text.splitlines()
                           if line.strip() and len(line.strip()) > 15), text[:80])
        jobs.append(Job(
            source="linkedin_posts",
            external_id=activity_id,
            title=f"[post] {first_line[:120]}",
            company=author,
            location="",
            url=f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/",
            description=text,
            posted_at=None,  # feed não expõe data confiável; recência fica neutra
        ))
    return jobs


class LinkedInPostsSource(JobSource):
    name = "linkedin_posts"
    requires_auth = True

    def __init__(self, config: PostsConfig):
        self.config = config
        self._requests_made = 0

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        if self.config.mode != "browser":
            log.info("linkedin_posts: modo %r — nada a fazer", self.config.mode)
            return
        if self._requests_made >= self.config.max_requests_per_run:
            log.info("linkedin_posts: limite de requisições da execução atingido")
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright não instalado. Rode: pip install -e '.[posts]' "
                      "&& playwright install chromium")
            return

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(self.config.cdp_url)
            except Exception as exc:
                log.error(
                    "Não conectou ao Chrome em %s (%s). Abra o Chrome com "
                    "--remote-debugging-port=9222 e tente de novo.",
                    self.config.cdp_url, exc)
                return
            context = browser.contexts[0] if browser.contexts else None
            if context is None:
                log.error("Chrome sem contexto/perfil ativo")
                return

            page = context.new_page()
            try:
                url = SEARCH_URL.format(kw=urllib.parse.quote(filters.keywords))
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._requests_made += 1
                if "/login" in page.url or "authwall" in page.url:
                    log.error("linkedin_posts: sessão não está logada — abortando")
                    return
                # Ritmo humano: espera, rola algumas vezes devagar
                time.sleep(random.uniform(3, 6))
                for _ in range(3):
                    page.mouse.wheel(0, random.randint(600, 1200))
                    time.sleep(random.uniform(2, 5))
                posts = page.evaluate(EXTRACT_JS)
            finally:
                page.close()

        jobs = extract_jobs_from_posts(posts, filters.keywords)
        log.info("linkedin_posts: %d posts na tela, %d com cara de vaga",
                 len(posts), len(jobs))
        yield from jobs
