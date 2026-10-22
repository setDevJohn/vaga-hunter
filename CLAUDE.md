# Regras do projeto vaga-hunter

Bot pessoal de busca de vagas. Perfil: dev fullstack/front/back junior-pleno;
mobile só React Native; devops/cloud só junior. Risco de bloqueio ~zero, sem
login (exceto LinkedIn posts via CDP, opcional e desligado por padrão).

## Fluxo de trabalho

- Implementar em branch `feature/*`. **NUNCA mergear na main nem fazer push sem
  pedido explícito do usuário na conversa atual.** Fluxo: implementar → validar
  com subagente → usuário testa manualmente → ele manda mergear/pushar.
- Segredos (token/chat_id do Telegram) só via env
  (`VAGA_HUNTER_TELEGRAM_TOKEN`/`_CHAT_ID`). Nunca no git.
- Testes com fixtures reais capturadas, sem rede. Suite inteira verde antes de
  qualquer commit.

## Antes de implementar uma fonte nova (OBRIGATÓRIO)

1. **Verificar WAF/CDN**: olhar headers (`server`, `cf-ray`, `via`, `x-cache`)
   e testar a tolerância de requisições — alguns requests espaçados e um
   pequeno burst. Se aparecer página de desafio (reCAPTCHA, "Verificação de
   segurança") ou 403/429 com poucos requests, a fonte é **inviável — não
   implementar**. Nunca tentar contornar captcha/desafio de WAF.
2. **Exigir ordenação por data**: a listagem precisa vir (ou aceitar parâmetro
   para vir) ordenada do mais recente para o mais antigo — é o que garante
   pegar sempre as vagas novas e permite early-break. Exceção aceitável: fonte
   sem paginação que entrega tudo num request (ex.: sitemap do Trampos).
   Fonte paginada sem ordenação por data: não implementar.
3. **Preferir dados estruturados**: API/JSON interno, `_next/data` (Next.js),
   `__NUXT_DATA__` (Nuxt), JSON-LD JobPosting ou sitemap — antes de parsear
   HTML de card. Menos frágil e geralmente evita request de detalhe.
4. **Checar se a descrição vem na listagem**: se vier, `skip_detail` vira no-op
   e a fonte faz 1 request por página.

## Regras das fontes já validadas (seguir nas novas)

- **Sessão própria por fonte** (`PoliteSession`): delays/circuit breaker
  independentes por site. Delays padrão 2–6s; dobrar para site sensível.
- **403 de WAF não é erro transitório**: entra em `block_status` da sessão e,
  em WAF por reputação de IP, usar `breaker_threshold=1` — repetir o request
  só piora o bloqueio. Cooldown persiste no banco (padrão 6h).
- **Corte por período (`time_range`)**: vaga fora da janela NÃO entra no
  `_yielded` (query posterior com janela maior ainda pode entregá-la).
  Early-break quando a página inteira está fora da janela ou toda duplicada —
  SÓ em listagem ordenada por data.
- **Dedup em dois níveis**: `_yielded` por instância (entre queries do mesmo
  run; fontes são recriadas a cada run) + banco por uid e fingerprint
  (título+empresa normalizados).
- **Match por slug** (quando a busca é por slug na URL): token exato ou
  concatenação de pares adjacentes nos 2 sentidos — nunca substring
  ("react" não pode casar "preact").
- **Datas**: sempre timezone-aware; data relativa ("há N dias") vira meio-dia
  BRT; sem data → `posted_at=None` (recência neutra no scoring).
- **Parser como função pura** (`parse_*`) testável com fixture real; logar
  warning quando página não-vazia parseia 0 vagas (formato pode ter mudado).

## Estado de WAF das fontes ativas (out/2026)

| Fonte | CDN/WAF | Observação |
|---|---|---|
| LinkedIn (guest) | Cloudflare | rate-limit 429/999 tratado; volume baixo ok |
| InfoJobs | — | sem bloqueio observado |
| Catho | nginx | 403 só com Referer localhost no navegador (links usam noreferrer) |
| Programathor | Cloudflare (proxy) | sem desafio hoje; detalhe dá 500 às vezes (fallback card) |
| Trampos.co | nginx | sitemap público, sem bloqueio |
| Gupy | CloudFront | sem desafio; buildId renovado em 404 de deploy |
| Remotar | — | API aberta; param é `search` ("q" é ignorado) |

## Fontes analisadas e DESCARTADAS (não reimplementar sem o usuário pedir)

- **Indeed** — Cloudflare com desafio; só viável com navegador real (CDP).
  Analisado e não implementado.
- **Vagas.com.br** — implementado e removido (2026-10-22): busca retorna
  landing pages irrelevantes no lugar de vagas + rate-limit 429.
- **Empregos.com.br (Solides)** — implementado e removido (2026-10-22): WAF
  GoCache bloqueia por reputação de IP com desafio reCAPTCHA; já bloqueava
  paginação/ordenação, depois o site inteiro com poucos requests. Inviável
  sem navegador real.

A lista acima também aparece na UI (Configurações → Descartadas) — manter as
duas em sincronia.
