# vaga-hunter

Bot em Python que busca, filtra e **ranqueia** vagas de emprego recentes de acordo com o seu perfil, com deduplicação entre execuções e notificações via Telegram e desktop.

Fontes: **LinkedIn** e **InfoJobs** (buscas públicas, sem login — zero risco para contas). Arquitetura pluggável para adicionar Catho, Indeed etc.

## Instalação

```bash
cd vaga-hunter
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml   # edite conforme seu perfil
```

## Uso

```bash
vaga-hunter run              # busca única, mostra tabela ranqueada e notifica novas
vaga-hunter run --dry-run    # busca sem gravar nem notificar
vaga-hunter serve            # interface web em http://127.0.0.1:5000 (busca manual + toggle do daemon)
vaga-hunter daemon           # daemon de buscas periódicas direto no terminal
vaga-hunter export --format csv|json
```

## Notificações

- **Telegram**: crie um bot no [@BotFather](https://t.me/BotFather) e exporte:
  ```bash
  export VAGA_HUNTER_TELEGRAM_TOKEN="123:abc"
  export VAGA_HUNTER_TELEGRAM_CHAT_ID="999"
  ```
- **Desktop**: usa `notify-send` (padrão em desktops Linux).

Só vagas **novas** com score ≥ `scoring.threshold` geram notificação.

## Scoring

Regras transparentes (0–100), pesos configuráveis em `config.toml`:
role no título, senioridade (junior/pleno bonificados; "senior" penaliza; "estágio/trainee" zera),
keywords include/exclude, preferência de remoto e recência da vaga.
Regras especiais: mobile só pontua com React Native; devops/cloud só junior.

## Fonte de posts do LinkedIn (opcional)

⚠️ A busca em **posts** exige sessão logada. O modo `browser` controla um Chrome local via CDP, com ritmo humano e pouquíssimas requisições por execução. O risco de bloqueio é baixo, **mas não zero** — por isso vem **desligado por padrão** (`linkedin_posts = false`). A busca pública de vagas não usa a sua conta.

Como o Chrome 136+ bloqueia a porta de depuração no perfil padrão, o bot usa um **perfil próprio** (`~/.config/vaga-hunter-chrome`). Clique no pill "Posts" da interface: ele abre esse Chrome sozinho — no primeiro uso, faça login no LinkedIn nessa janela (fica salvo) e clique no pill de novo.

## Anti-bloqueio

Delays aleatórios entre requisições, rotação de User-Agent, retries limitados com backoff,
circuit breaker em respostas 429/999 com cooldown persistido (o daemon não insiste).

## Testes

```bash
pytest
```

Sem rede: parsing testado com fixtures HTML reais salvas em `tests/fixtures/`.
