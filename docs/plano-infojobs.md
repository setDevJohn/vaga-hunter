# Plano de implementação: fonte InfoJobs

Branch: `feature/infojobs`. Recon feito em 2026-10-21 com fixtures reais salvas em
`tests/fixtures/infojobs_search.html` e `tests/fixtures/infojobs_detail.html`.

## O que o recon confirmou

- **Sem bloqueio**: HTTP 200 com UA de navegador, HTML completo server-side (sem JS).
- **Busca**: `https://www.infojobs.com.br/empregos.aspx?palabra=<query>&sortby=PublishedDate`
  — ordenação por data **funciona** (Hoje, Hoje, Ontem, ...).
- **Card** (`div.js_rowCard`):
  - id da vaga: `div.js_vacancyLoad[data-id]` (ex.: `12140150`) e `data-href` com a URL
  - título: `h2.js_vacancyTitle`
  - **data exata**: `div.js_date[data-value="2026/10/21 01:28:00"]` (timestamp completo — recência precisa, melhor que o LinkedIn)
  - empresa: âncora `a[href*="/empresa-"]` (texto; pode ser "Confidencial" sem link)
  - localização: investigar no fixture durante implementação (bloco abaixo da empresa)
- **Detalhe** (`/vaga-de-...__<id>.aspx`): HTTP 200, título em `og:title` ("Vaga: ..."),
  descrição presente, menções a "Home Office"/"CLT"/salário no corpo.
- Paginação: investigar parâmetro (`page=`/`p=`) na implementação; cards por página ≈ 12.

## Implementação

1. **`src/vaga_hunter/sources/infojobs.py`** — `InfoJobsSource(JobSource)`, `name="infojobs"`, `requires_auth=False`:
   - `build_params(filters, page)`: `palabra`=query; `sortby=PublishedDate` sempre;
     localização: mapear `filters.location` para o filtro de UF/cidade do site se simples,
     senão incluir na `palabra`; paginação via parâmetro descoberto.
   - `parse_search_page(html) -> list[Job]` (pura): seletores acima; `posted_at` do
     `js_date[data-value]` (formato `%Y/%m/%d %H:%M:%S`); `external_id` = `data-id`;
     `url` absoluta a partir do `data-href`.
   - `parse_job_detail(html) -> str` (pura): descrição do corpo da vaga.
   - `search()`: mesmo contrato do LinkedIn — `PoliteSession`, `skip_detail` por uid
     conhecido, para em página vazia ou `max_pages`.
   - `time_range`: InfoJobs não tem filtro de período na URL conhecido → filtrar
     client-side por `posted_at` (descarta vagas mais velhas que o `time_range` ativo).
2. **Registry** (`pipeline.build_sources`): bloco `if config.sources.infojobs:` criando a
   fonte com sessão própria (delays independentes do LinkedIn).
   `config.py`: `SourcesConfig.infojobs: bool = False`; `[sources] infojobs = true` nos TOMLs.
3. **UI**: `SOURCE_PT` ganha `infojobs: "InfoJobs"` (badge automática no card).
4. **Dedup entre sites**: já coberto pelo fingerprint título+empresa (sem mudança).
5. **Scoring**: agnóstico de fonte (sem mudança); `detect_modality` já pega "Home Office".

## Testes

- `tests/test_infojobs_parsing.py`: parse do fixture de busca (n cards, id, título,
  empresa, data exata) e do detalhe (descrição > 100 chars); página vazia → [].
- `test_build_params`: palabra/sortby/paginação.
- Paginação com sessão fake (mesmo padrão do teste do LinkedIn).
- Filtro client-side de `time_range` com datas controladas.
- E2E manual: `vaga-hunter run --query "desenvolvedor fullstack"` com
  `[sources] infojobs = true` → vagas com badge InfoJobs no painel; rerun deduplica;
  vaga repetida LinkedIn×InfoJobs não duplica.

## Riscos

- Localização/paginação precisam de 1 rodada extra de recon na implementação (params
  exatos) — baixo risco, site é server-rendered.
- Markup `js_*` parece estável (classes funcionais de JS do próprio site), mas parsers
  puros + fixtures tornam a manutenção barata, como no LinkedIn.

## Ordem de commits na branch

1. fixtures + parsers puros + testes (sem rede)
2. source + registry + config
3. badge UI + README
4. ajustes pós-E2E manual
