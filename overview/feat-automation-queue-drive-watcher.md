# Overview — feat/automation-queue-drive-watcher

## 1. Branch
`feat/automation-queue-drive-watcher` (off `main`).

## 2. Objetivo
Implementar a camada de automação sobre o worker (PostgreSQL fonte da verdade,
Redis fila/lock): auto-poll por usuário, Drive watcher por polling, fila Redis
avançada, concorrência por provider (cloud configurável padrão 5 / local 1),
retry/backoff com `next_retry_at`, dead-letter, guardrails de custo e
observabilidade da fila — **sem criar um sexto container** e sem quebrar o CLI legado.

## 3. Arquivos criados
- `app/transcription/provider_kind.py` — classificação cloud/local por provider resolvido.
- `app/services/drive_watcher.py` — watcher multi-arquivo (`poll_user` + `PollResult`).
- `app/services/guardrails.py` — `Guardrails` + `resolve_guardrails`.
- `app/worker/auto_poll.py` — thread de auto-poll (`run_auto_poll_loop` + `auto_poll_tick`).
- `alembic/versions/0002_automation_and_retry.py` — migration.
- `app/web/templates/automation_settings.html`, `app/web/templates/queue_status.html`.
- `documentation/28-auto-polling.md`, `29-redis-queue-advanced.md`,
  `30-provider-concurrency.md`, `31-retries-dead-letter.md`, `32-cost-guardrails.md`.
- `docs/superpowers/specs/2026-06-05-automation-queue-drive-watcher-design.md`,
  `docs/superpowers/plans/2026-06-05-automation-queue-drive-watcher.md`.
- Testes: `tests/test_provider_kind.py`, `tests/test_automation_repository.py`,
  `tests/test_drive_watcher.py`, `tests/test_guardrails.py`, `tests/test_auto_poll.py`.
- `overview/feat-automation-queue-drive-watcher.md` (este arquivo).

## 4. Arquivos alterados
- Domínio/erros: `app/errors.py` (`classify_error`, `error_code`/`retryable`,
  `DeepgramRateLimitError`/`ProviderKeyInvalidError`/`FileTooLargeError`),
  `app/deepgram_client.py` (mapeamento 429/401/413).
- Dados: `app/database/models.py` (`TranscriptionJob.next_retry_at`/`last_error_code`
  + índices; nova tabela `UserAutomationSettings`), `app/core/models.py`
  (`Job` + `AutomationSettings`), `app/core/ports.py` (`JobRepository` estendido +
  `AutomationSettingsRepository` + `Repositories.automation`),
  `app/repositories/memory.py`, `app/repositories/postgres.py`.
- Fila: `app/queue/ports.py`, `app/queue/redis_queue.py`, `app/queue/memory_queue.py`,
  `app/queue/__init__.py` (`requeue_pending_jobs(now)`), `app/queue/config.py`.
- Worker: `app/worker/config.py`, `app/worker/container.py`, `app/worker/processor.py`
  (`resolve`/`ResolvedProvider`/`_handle_failure`/`_backoff`), `app/worker/queue_loop.py`,
  `app/worker/main.py`.
- Drive/Web: `app/drive_client.py` (`is_ready_media_file` + áudio), `app/web/main.py`
  (rotas `/settings/automation`, `/automation/check-now`, `/jobs/{id}/retry`,
  `/admin/queue`), `app/web/repositories.py` (`Job.last_error_code`), templates
  `base.html`/`settings.html`/`jobs.html`.
- Infra/Docs: `docker-compose.yml`, `.env.example`, `CLAUDE.md`, `README.md`,
  `documentation/03,09,11,19`.
- Testes ajustados: `tests/support.py`, `tests/test_errors.py`,
  `tests/test_deepgram_client.py`, `tests/test_models_metadata.py`,
  `tests/test_repositories_memory.py`, `tests/test_core_ports.py`,
  `tests/test_queue_memory.py`, `tests/test_queue_redis.py`,
  `tests/test_queue_requeue.py`, `tests/test_worker_processor.py`,
  `tests/test_worker_queue_loop.py`, `tests/test_drive_client.py`,
  `tests/test_worker_config.py`, `tests/test_queue_config.py`,
  `tests/test_web_routes.py`.

## 5. Migrations
`0002_automation_and_retry` (down_revision `0001_initial`):
adiciona `transcription_jobs.next_retry_at`, `transcription_jobs.last_error_code`,
índices `ix_transcription_jobs_status_next_retry` e `ix_transcription_jobs_user_created`,
e cria `user_automation_settings` (FK→users CASCADE, único por user, índice
`ix_user_automation_enabled_polled`). `(user_id, source_file_id)` já existia.

## 6. Variáveis de ambiente adicionadas
`CLOUD_TRANSCRIPTION_CONCURRENCY=5`, `LOCAL_TRANSCRIPTION_CONCURRENCY=1`,
`PROVIDER_LOCK_TTL_SECONDS=14400`, `TRANSCRIPTION_QUEUE_CONCURRENCY=5`,
`JOB_MAX_ATTEMPTS=3`, `JOB_RETRY_BASE_SECONDS=60`, `JOB_RETRY_MAX_SECONDS=3600`,
`AUTO_POLL_ENABLED=true`, `AUTO_POLL_INTERVAL_SECONDS=300`,
`AUTO_POLL_MAX_USERS_PER_TICK=50`, `AUTO_POLL_MAX_FILES_PER_USER=5`,
`AUTO_POLL_LOCK_TTL_SECONDS=240`, `MAX_FILE_SIZE_MB=0`, `DAILY_JOBS_LIMIT=0`
(0 = ilimitado). Todas com default seguro em `.env.example`/`docker-compose.yml`.

## 7. Testes adicionados/alterados
Cobrem: classificação cloud/local; classificação de erros + mapeamento Deepgram
429/401/413; colunas/tabela/migration; retry/guardrail/observability no repo
(memory+postgres); `AutomationSettingsRepository`; provider slots
(semáforo cloud 5/6º, lock local, reclaim de slot expirado, release token-safe);
sets dead/processing + `queue_stats`; `requeue_pending_jobs(now)` respeitando
backoff; watcher multi-arquivo + áudio; guardrails (tamanho + limite diário);
queue loop com slots + retry/dead-letter; auto-poll (cria/dedupe/lock/erro
amigável/sweep de retry); config from_env; rotas web (automation, check-now,
retry, admin queue).

## 8. Comandos executados
```bash
python -m pytest <subset por tarefa>      # TDD em cada passo
python -m compileall app scripts alembic
docker compose config                     # com .env (cp .env.example .env)
```
(Interpretador: o venv do repo principal — `…/meet-transcricao/.venv` — pois este
worktree compartilha o pacote `app`. A suíte completa de uma vez estoura memória
no ambiente; rodada em blocos.)

## 9. Resultado dos testes
Suíte completa (rodada em duas metades para não estourar memória):
**333 passed, 37 skipped, 0 failed** (skips = testes que exigem PostgreSQL e
pulam sem DB). `compileall app scripts alembic` OK; `docker compose config` OK;
`docker compose build` OK (web/worker/migrate). Uma revisão adversarial multi-agente
do diff encontrou 2 problemas reais (backoff Retry-After acima do teto; bookkeeping
do check-now sem try/except) — **ambos corrigidos** com testes de regressão.

## 10. Riscos e limitações
- Latência de retry limitada pela cadência do reconciler/auto-poll (não instantânea).
- TTL do slot cloud = `PROVIDER_LOCK_TTL_SECONDS` (4h): slot de worker morto só é
  reclamado após o TTL (conservador/seguro).
- Polling lista a pasta inteira a cada tick (Changes API adiada); mitigado por
  `last_poll_at` + dedupe no banco.
- `monthly_cloud_minutes_limit` e `max_file_duration_minutes` são *scaffold*
  (colunas + env existem; enforcement só onde há dado), documentados como próximos passos.

## 11. Como testar manualmente
1. `cp .env.example .env`, ajuste segredos; `docker compose up --build`.
2. Login (admin), conecte o Google, configure a pasta do Drive e a Deepgram key.
3. Em **Automação**: ative, defina intervalo/arquivos, salve; clique **Verificar agora**
   — novos vídeos viram jobs `pending` e são enfileirados.
4. Suba ≥6 vídeos para ver cloud rodando em paralelo (até 5) e os demais aguardando.
5. Force um 429/erro para ver retry com backoff; após esgotar tentativas, o job vai
   para dead-letter e aparece **Tentar novamente** em Jobs; o admin vê **Fila**.

## 12. Próximos passos
Drive Changes API + `drive_watch_state` (pageToken), metering completo de minutos,
contabilidade de custo por provider, escala horizontal além das threads do worker.

## 13. PR
Branch pronta e validada localmente. Push/PR a serem executados manualmente:

```bash
git push -u origin feat/automation-queue-drive-watcher
gh pr create --base main --head feat/automation-queue-drive-watcher \
  --title "Add automatic Drive polling and provider queue policies" \
  --body "Adds automatic polling, Redis queue policies, provider concurrency, retries, dead-letter and cost guardrails."
```
(Preencher o link do PR aqui após criá-lo.)

## 14. Confirmação explícita
- **Não reintroduziu SQLite** — sem `sqlite3`/`app.db`/`database_path`; fakes em
  dict; `0002` é Postgres (índices/Boolean/Text).
- **Não loga segredos** — tokens/keys continuam Fernet; erros usam `user_message`;
  nenhum traceback na UI; mensagens de erro amigáveis.
- **Web UI não processa transcrição pesada** — `/automation/check-now` apenas
  *lista* o Drive e cria jobs `pending` (mesma classe do Run once); download/
  transcrição/upload seguem exclusivamente no worker.
