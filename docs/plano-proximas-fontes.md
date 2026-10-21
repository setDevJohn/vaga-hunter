# Plano: próximas fontes de vagas

Recon feito em 2026-10-21 (curl com UA de navegador, sem login):

| Site | Status | Dificuldade |
|---|---|---|
| **InfoJobs BR** | HTTP 200, HTML completo com cards (`js_rowCard`), URLs de detalhe `/vaga-de-...__<id>.aspx` | **Baixa — próximo** |
| **Catho** | HTTP 200, mas dados não estão em `__NEXT_DATA__` simples; renderização JS/API interna | Média — investigar API XHR |
| **Indeed BR** | HTTP 403 Cloudflare já na primeira página | Alta — só via navegador real (CDP, como posts) |

## Fase A — InfoJobs (próxima)

1. **Fixtures**: salvar busca `empregos.aspx?palabra=<query>&provincia=...` e uma página de detalhe em `tests/fixtures/infojobs_*.html`.
2. **`sources/infojobs.py`** (`InfoJobsSource(JobSource)`, `name="infojobs"`):
   - `build_params`: `palabra` (query); mapear localização e, se houver, filtro de data/ordenação por recência (investigar params `ordenacion`/`sinceDate` na UI do site).
   - `parse_search_page(html)`: cards `.js_rowCard` → título, empresa, local, URL (`/vaga-de-...__<id>.aspx`), data; `external_id` = id numérico do final da URL.
   - `parse_job_detail(html)`: descrição completa.
   - Mesma `PoliteSession` (delays/backoff/circuit breaker) e `skip_detail` por uid já visto.
3. **Registry**: `build_sources` ganha bloco `if config.sources.infojobs:`; config `[sources] infojobs = true`.
4. **Dedup entre sites**: fingerprint título+empresa já cobre (vaga repetida LinkedIn×InfoJobs não duplica nem renotifica).
5. **Testes**: parsing com fixtures, build_params, source registrada. E2E manual: `vaga-hunter run` e conferir badge "InfoJobs" no card (adicionar ao `SOURCE_PT` do app.js).

Estimativa: ~1 sessão de trabalho.

## Fase B — Catho

1. Abrir busca no navegador com DevTools e capturar a chamada XHR/JSON que alimenta a lista (site é Next.js; a API interna provavelmente retorna JSON limpo).
2. Se a API for acessível com headers simples → fonte via `PoliteSession` com JSON (melhor que HTML).
3. Se exigir token/cookie dinâmico → reavaliar: modo CDP (navegador do bot) ou pular.

## Fase C — Indeed

Cloudflare bloqueia requests diretos. Única via: navegador real (mesmo esquema da fonte de posts — perfil dedicado + CDP + ritmo humano). Implementar por último; avaliar se vale (muitas vagas do Indeed são repost de outras fontes).

## Observações

- Cada fonte nova = 1 arquivo em `sources/` + 1 flag no config + 1 label no `SOURCE_PT`. Pipeline, scoring, dedup, notificação e UI não mudam.
- Scoring é agnóstico de fonte; `detect_modality` idem.
