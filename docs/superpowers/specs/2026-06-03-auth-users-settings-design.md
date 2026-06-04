# Design: Autenticação, Usuários, Admin, OAuth e Settings por Usuário (PostgreSQL)

Branch: `feat/auth-users-settings` (worktree `…/meet-transcription-auth`).

## Objetivo

Transformar o app de **single-admin** (login por env) em **multiusuário controlado por admin**, sobre **PostgreSQL** como única fonte de verdade. Esta branch entrega regras de negócio, rotas, formulários, serviços e o **contrato de persistência** (Protocols/repositories), consumindo a camada Postgres que a branch `postgres-core` implementará. Enquanto essa camada não existe, esta branch trabalha contra **interfaces** e usa **fakes em memória** nos testes.

## Decisão arquitetural (final)

- **Sem SQLite.** Remover `app/db.py` e qualquer `sqlite3` como camada ativa. Sem `init_db`, sem migração SQLite.
- **PostgreSQL é a única fonte de verdade.** As tabelas e o repository real são de `postgres-core` (SQLAlchemy).
- Esta branch define **Protocols** (contrato) + **dataclasses de domínio** storage-agnósticas, e liga tudo por **injeção de dependência**. Testes injetam **fakes em memória** (dict puro — **nunca** SQLite in-memory).
- Persistência real fica como **contrato pronto** para integração com `postgres-core` (que pode adicionar testes contra Postgres real).

## Fronteiras entre branches (multi-terminal)

- **auth (esta):** mantém as rotas vivas; só **enfileira** jobs (valida pré-condições e cria job `pending`). Dona de users/roles/oauth/deepgram-creds/drive-settings.
- **postgres-core:** dona da **tabela** `transcription_jobs` e do **repository real** (todas as tabelas).
- **postgres-worker:** dona do **comportamento real de processamento** (download → transcrição → persistência do transcript), downloads e `transcript_text`.
- **ui-devops-polish:** acabamento visual/devops.

## Arquitetura: Repositories + Injeção de Dependência

### Camada de contrato — `app/web/repositories.py`

Dataclasses de domínio (imutáveis, sem dependência de armazenamento):

```python
@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str | None
    role: str            # "admin" | "user"
    is_active: bool
    google_email: str | None
    google_name: str | None

@dataclass(frozen=True)
class GoogleToken:        # campos sensíveis já cifrados quando vêm do store
    access_token: str
    refresh_token: str | None
    token_uri: str
    client_id: str
    client_secret: str | None
    scopes: str
    expiry: str | None

@dataclass(frozen=True)
class DriveSettings:
    source_drive_folder_url: str
    source_drive_folder_id: str
    destination_drive_folder_url: str | None
    destination_drive_folder_id: str | None
    save_copy_to_drive: bool

@dataclass(frozen=True)
class Job:
    id: int
    user_id: int
    source_file_id: str | None
    source_file_name: str | None
    transcript_drive_file_id: str | None
    status: str
    error_message: str | None
    attempts: int
    created_at: str
    updated_at: str
    processed_at: str | None
```

`Protocol` por repositório (PEP 544). Assinaturas que a `postgres-core` deve satisfazer:

```python
class UsersRepository(Protocol):
    def get_by_email(self, email: str) -> User | None: ...
    def get_by_id(self, user_id: int) -> User | None: ...
    def list_all(self) -> list[User]: ...
    def create(self, *, email: str, password_hash: str, role: str, name: str | None = None) -> User: ...
    def set_active(self, user_id: int, active: bool) -> None: ...
    def set_password_hash(self, user_id: int, password_hash: str) -> None: ...
    def set_google_identity(self, user_id: int, google_email: str | None, google_name: str | None) -> None: ...
    def ensure_admin(self, *, email: str, password_hash: str) -> User: ...   # bootstrap idempotente
    # uso interno do login/admin: precisa do hash; expor via método dedicado p/ não vazar em User
    def get_password_hash(self, user_id: int) -> str | None: ...

class GoogleTokensRepository(Protocol):
    def get_for_user(self, user_id: int) -> GoogleToken | None: ...
    def save_for_user(self, user_id: int, token: GoogleToken) -> None: ...

class DeepgramCredentialsRepository(Protocol):
    def get_encrypted_for_user(self, user_id: int) -> str | None: ...   # ciphertext ou None
    def save_for_user(self, user_id: int, api_key_encrypted: str) -> None: ...

class DriveSettingsRepository(Protocol):
    def get_for_user(self, user_id: int) -> DriveSettings | None: ...
    def save_for_user(self, user_id: int, settings: DriveSettings) -> None: ...

# Subconjunto MÍNIMO compatível com o JobRepository do Terminal 3 (postgres-worker).
# NÃO inventar nomes paralelos. Nomes reaproveitados: create_job, list_jobs_for_user.
class TranscriptionJobsRepository(Protocol):
    def create_job(self, *, user_id: int, status: str = "pending",
                   source_file_id: str | None = None,
                   source_file_name: str | None = None) -> Job: ...
    def list_jobs_for_user(self, user_id: int, limit: int | None = None) -> list[Job]: ...
    def find_active_for_user(self, user_id: int) -> Job | None: ...   # pending/processing
```

Container agregador:

```python
@dataclass(frozen=True)
class RepositoryBundle:
    users: UsersRepository
    google_tokens: GoogleTokensRepository
    deepgram_credentials: DeepgramCredentialsRepository
    drive_settings: DriveSettingsRepository
    jobs: TranscriptionJobsRepository
```

### Wiring de produção e fakes

- `create_app(settings, repositories: RepositoryBundle | None = None)`. Testes injetam um `RepositoryBundle` de fakes.
- Sem repos injetados, `create_app` chama `build_repositories(settings)` (entrypoint acordado) que tenta importar a implementação Postgres de `postgres-core` em `app/db/postgres.py::build_repositories(database_url)`. Se ausente, **falha no startup** com mensagem clara: *"Camada PostgreSQL (postgres-core) indisponível: integre a branch postgres-core para rodar o app web."* Isso torna o ponto de integração explícito (o app web não sobe sem Postgres — consequência aceita da decisão de arquitetura).
- **Fakes** vivem em `tests/fakes.py` (`InMemoryUsersRepository`, etc. + `build_fake_repositories()`), dict-backed.

### Criptografia (Fernet) na aplicação, não no Postgres

`app/web/security.py` (já existe) deriva Fernet de `APP_SECRET_KEY`. Stores de aplicação envolvem os repos e cifram/decifram:
- `TokenStore` (existente, refatorado p/ usar `GoogleTokensRepository`): cifra access/refresh/client_secret antes de `save_for_user`; decifra ao ler.
- `DeepgramKeyStore` (novo): cifra a key antes de `save_for_user`; decifra ao ler; expõe `get_key(user_id) -> str | None`, `has_key(user_id) -> bool`, `masked(user_id) -> str | None` (últimos 4).
Repos só persistem **strings já cifradas** → camada Postgres permanece storage-agnóstica.

## Modelo de dados — alvo PostgreSQL (definido por `postgres-core`, NÃO criado aqui)

- `users`: id, email (unique, not null), name, password_hash (not null), role (`admin|user`, default `user`), is_active (bool, default true), google_email, google_name, created_at, updated_at.
- `google_tokens`: id, user_id (unique FK), access_token (cifrado), refresh_token (cifrado), token_uri, client_id, client_secret (cifrado), scopes, expiry, created_at, updated_at.
- `deepgram_credentials`: id, user_id (unique FK), api_key (cifrado, not null), created_at, updated_at.
- `user_drive_settings`: id, user_id (unique FK), source_drive_folder_url (not null), source_drive_folder_id (not null), destination_drive_folder_url (null), destination_drive_folder_id (null), save_copy_to_drive (bool, default false), created_at, updated_at.
- `transcription_jobs`: **dona = postgres-core/postgres-worker.** Esta branch só consome o contrato mínimo.

## Autenticação, Roles e Bootstrap Admin

- `app/web/passwords.py`: `hash_password(plain) -> str`, `verify_password(plain, hashed) -> bool` com **passlib[bcrypt]** (única dependência nova nesta branch; SQLAlchemy/driver Postgres são da postgres-core).
- **Bootstrap admin** no startup (lifespan): `repositories.users.ensure_admin(email=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD))`. Idempotente: se já existe admin com esse email, não duplica; garante `role='admin'`, `is_active=true` e senha definida. `ADMIN_USERNAME`/`ADMIN_PASSWORD` continuam só para bootstrap.
- **Login** (`POST /login`): busca por email; checa `is_active`; `verify_password` contra `get_password_hash`. Inválido/desativado → 401 com erro. Form mantém campos compatíveis (o identificador de login é o email; o admin bootstrap usa `ADMIN_USERNAME` como email).
- `require_user` (existente, refatorado p/ usar `users` repo). `require_admin` encadeia `require_user` e retorna **403** se `role != 'admin'`.

## Rotas e Permissões

Admin (`require_admin`):
- `GET /admin/users` — lista usuários + formulário de criação.
- `POST /admin/users` — cria usuário (email, password, role). Senha hash.
- `POST /admin/users/{user_id}/disable` — `set_active(False)`.
- `POST /admin/users/{user_id}/enable` — `set_active(True)`.
- `POST /admin/users/{user_id}/reset-password` — define nova senha (campo simples no form da lista) e hash.

Settings (`require_user`):
- `GET /settings/deepgram`, `POST /settings/deepgram`, `POST /settings/deepgram/test`.
- `GET /settings/drive`, `POST /settings/drive`.
- `GET /settings` → redirect 303 para `/settings/drive` (compat).

Protegidas (já existentes, mantidas): `GET /` (dashboard), `GET /jobs`, `POST /jobs/run-once`, `GET /connect-google`, `GET /oauth/google/callback`. Públicas: `GET /health`, `GET/POST /login`, `POST /logout`.

## Deepgram por usuário

- Chave **exigida por usuário**, **sem fallback para env**.
- `POST /settings/deepgram`: cifra e salva via `DeepgramKeyStore`. Nunca exibir a chave depois — UI mostra **"Configured"** ou **últimos 4**.
- `POST /settings/deepgram/test`: ping leve real `GET https://api.deepgram.com/v1/projects` com `Authorization: Token <key>`, timeout curto (~5s), defensivo:
  - 200 → "válida"; 401/403 → "inválida"; exceção de rede/timeout/5xx → **"não foi possível verificar agora"**. Nunca marca inválida como válida; nunca loga a chave. Helper em `app/web/deepgram_key.py::verify_deepgram_key(key) -> Literal["valid","invalid","unverifiable"]`.
- run-once **bloqueia** sem chave com exatamente: **"Configure sua Deepgram API Key antes de iniciar uma transcrição."**

## Settings de Drive (por URL)

- `extract_google_drive_folder_id(value: str) -> str` em **módulo puro** `app/web/drive_links.py`. Aceita:
  - `https://drive.google.com/drive/folders/<ID>`
  - `https://drive.google.com/drive/folders/<ID>?usp=sharing` (querystring)
  - `https://drive.google.com/drive/u/0/folders/<ID>`
  - ID puro `<ID>`
  Extrai o `<ID>`, descarta querystring, valida `^[A-Za-z0-9_-]{10,}$`. `ValueError` se não extrair/validar.
- `POST /settings/drive`: `source_drive_folder_url` **obrigatório** → extrai `source_drive_folder_id`; `destination_drive_folder_url` **opcional** → extrai `destination_drive_folder_id` se presente; `save_copy_to_drive` opcional (checkbox). Salva via `DriveSettingsRepository`.
- **Drive = entrada + backup opcional**, não fluxo principal. Persistência principal do transcript (banco/download) é da postgres-worker.

## Jobs (contrato mínimo + fake)

- Esta branch **não cria** tabela de jobs.
- `/jobs` lista via `jobs.list_jobs_for_user(user_id)`. Dashboard usa `list_jobs_for_user(user_id, limit=5)`.
- `/jobs/run-once`: valida `source` (drive settings) + Google conectado + **Deepgram key**; se faltar key → mensagem de bloqueio. Se já houver job ativo (`find_active_for_user`) → "já existe um job em execução". Caso ok → `jobs.create_job(user_id, status="pending")` e mensagem "Job enfileirado; o worker fará o processamento." **Sem execução pesada nesta branch** (removido o processamento acoplado a SQLite/`app.db`; é da postgres-worker).

## UI / Navegação

`base.html`: Dashboard · Jobs · Drive Settings · Deepgram · **Admin Users** (só `role=admin`) · Logout. Templates novos: `admin_users.html`, `settings_drive.html`, `settings_deepgram.html`. Funcional, sem capricho visual.

## Segurança

- Nunca logar: Deepgram key, access_token, refresh_token, `APP_SECRET_KEY`, senha. Tudo sensível cifrado com Fernet (`APP_SECRET_KEY`).
- Cookies: `HttpOnly`; `Secure` quando `SESSION_COOKIE_SECURE=true` (já configurado via `SessionMiddleware`).
- OAuth com `state` (CSRF) já existente, mantido.

## Compatibilidade

- Worker CLI intacto: `python -m app.main --once`, `--watch`, `--once --reprocess <file_id>` continuam usando `app/config.py` + estado JSON. Nenhuma mudança em `app/main.py`, `app/processor.py`, `app/state.py`, `app/config.py`. Toda a web auth fica isolada em `app/web/`.

## Mudanças de Configuração

- `WebSettings`: remover `deepgram_api_key` (sem fallback env). Trocar `database_path: Path` (SQLite) por `database_url: str` (DSN Postgres, repassado ao `build_repositories`). Manter `tmp_dir`. `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `APP_SECRET_KEY`, `SESSION_COOKIE_SECURE`, `GOOGLE_WEB_*`, `GOOGLE_REDIRECT_URI` mantidos.
- `requirements.txt`: adicionar `passlib[bcrypt]`. **Não** adicionar SQLAlchemy/psycopg aqui (são da postgres-core).
- `.env.example`: documentar que `DATABASE_URL` agora é DSN Postgres e que Deepgram key é por usuário (na UI), não env.

## Testes e Validação

- **Sem SQLite in-memory.** Fakes dict-backed em `tests/fakes.py`. FastAPI `TestClient` com `RepositoryBundle` de fakes injetado.
- Reescrever `tests/test_web_routes.py`, `tests/test_web_services.py`, `tests/test_token_store.py` para fakes/DI. **Remover** `tests/test_db.py` (SQLite morto).
- Novos testes:
  - `passwords`: hash ≠ plain; `verify_password` true/false.
  - bootstrap admin: criado no startup; idempotente; login admin funciona.
  - admin cria usuário; novo usuário loga; aparece na lista.
  - `require_admin`: usuário comum recebe 403 em `/admin/*`.
  - usuário desativado não loga.
  - `extract_google_drive_folder_id`: 4 formatos válidos + inválido (`ValueError`).
  - Deepgram key: salva cifrada (ciphertext ≠ plaintext; round-trip decifra); UI mostra máscara; run-once exige key (mensagem exata).
  - Drive settings: `POST /settings/drive` extrai e persiste url+id+save_copy; destino opcional.
  - OAuth callback: token + userinfo mockados gravam `google_email`/`google_name`; valida `state`.
- Validação: `python -m pytest -v`, `python -m compileall app scripts`, `docker compose config`, `docker compose build`.

## Pontos de Integração

- **postgres-core** implementa `app/db/postgres.py::build_repositories(database_url) -> RepositoryBundle` satisfazendo os Protocols deste design; cria as tabelas (`users`, `google_tokens`, `deepgram_credentials`, `user_drive_settings`, `transcription_jobs`).
- **postgres-worker** implementa o processamento real (consome jobs `pending`) e a persistência/download do transcript. Reaproveita os nomes do `JobRepository` (create_job, list_jobs_for_user, …); o subconjunto desta branch é compatível.
- Nomenclatura de jobs alinhada ao Terminal 3 para evitar dois padrões.

## Fora de Escopo

- SQLite (qualquer uso). PostgreSQL real (postgres-core). Worker multiusuário completo / processamento real (postgres-worker). `transcript_text` e download (postgres-worker). Redesign visual avançado. GitHub Actions / GHCR.

## Critérios de Sucesso

- Zero SQLite/`app.db`/`sqlite3` no código.
- Login por usuário do banco (via repo/fake), senha hash, `is_active` respeitado.
- Bootstrap admin cria admin no contrato Postgres (fake nos testes), idempotente.
- Admin cria/lista/ativa/desativa/reseta-senha de usuários; usuário comum bloqueado em `/admin` (403).
- OAuth por usuário salva tokens cifrados + `google_email`/`google_name`.
- Deepgram key por usuário, cifrada, exigida (sem env fallback), com máscara e teste best-effort.
- Drive settings por URL com extração de folder id; destino opcional; save_copy opcional.
- `/jobs` e `/jobs/run-once` vivos via contrato + fake; run-once só enfileira.
- `pytest`, `compileall`, `docker compose config`, `docker compose build` verdes.
- Contrato (Protocols + RepositoryBundle + entrypoint `build_repositories`) pronto para postgres-core.
