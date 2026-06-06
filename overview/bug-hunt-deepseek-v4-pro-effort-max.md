# Bug Hunt Report — DeepSeek V4 Pro Effort Max

## 1. Executive summary

- **Status geral:** BUILD OK. Testes: 659 passed, 41 skipped, 0 failures. Docker compose renderiza 5 serviços. Alembic 1 head.
- **Pode mergear agora? NÃO** — há bugs de lógica que quebram o onboarding/dashboard para providers não-Deepgram.
- **Bloqueadores:** 3 (BUG-CRIT-001, BUG-CRIT-002, BUG-CRIT-003)
- **Riscos altos:** 4
- **Riscos médios:** 6
- **Observações:** A arquitetura de providers está correta no worker (processor.py), mas a UI (main.py rotas de dashboard/onboarding, templates) ainda usa lógica hardcoded Deepgram, ignorando o registry de providers.

---

## 2. Environment

- **Branch:** qa/next-platform-features-v2
- **Commit:** 009fa11 (bypass select_backend check in test runner and align FfmpegNotFoundError message)
- **Data/hora:** 2026-06-06
- **Python:** 3.12.3
- **Docker:** 29.1.3
- **Compose:** 2.40.3
- **Alembic head:** 0002_transcript_fts (single head)

---

## 3. Validation results

| Comando | Resultado | Observações |
|---------|-----------|-------------|
| `alembic heads` | 0002_transcript_fts | 1 head, sem branches |
| `python -m pytest -v` | 659 passed, 41 skipped, 0 failures | 78.10s |
| `python -m pytest tests/e2e -v` | N/A (e2e require live services) | Não executado |
| `python -m compileall app scripts` | OK | Sem erros |
| `docker compose config` | 5 serviços renderizados | postgres, redis, migrate, web, worker |
| `docker compose build` | N/A | Não executado |

---

## 4. Critical findings

### BUG-CRIT-001 — Onboarding usa lógica Deepgram hardcoded, ignora registry de providers

- **Severidade:** CRITICAL
- **Categoria:** Provider readiness / Onboarding / UI
- **Arquivos:**
  - `app/web/main.py:372-412` (rota `/onboarding`)
  - `app/web/templates/onboarding.html`
  - `app/web/templates/dashboard.html:7,37,42`
  - `app/web/templates/jobs.html:22,25`
- **Funções/linhas:**
  - `main.py:374` — `provider_ready = (not status.deepgram_required) or deepgram_configured`
  - `main.py:392` — provider_label hardcoded `"Deepgram configurado"`
  - `main.py:409` — CTA hardcoded `("Configurar Deepgram", "/settings/deepgram")`
  - `main.py:411` — descrição hardcoded `"Valide a chave Deepgram ou o modelo local"`
  - `main.py:395` — test_cta hardcoded `("Testar Deepgram", "/settings/deepgram")`
- **Evidência:**
  ```
  # app/web/main.py:372-374
  deepgram_configured = deepgram_store.has_key(user.id)
  provider_ready = (not status.deepgram_required) or deepgram_configured
  ```
  - `status.deepgram_required` vem de `app.state.transcription_status`, que é computado por `get_transcription_provider_status(TranscriptionConfig.from_env())` (linha 136-139). Este SO analisa a config LOCAL do ambiente, NÃO o registry de providers.
  - Se local está disabled (padrão), `deepgram_required=True` SEMPRE. Mesmo que o usuário tenha OpenRouter com key salva no registry.
  - Aí `provider_ready = (not True) or deepgram_configured` → SÓ fica true se Deepgram tem key.
- **Como reproduzir:**
  1. Configurar OpenRouter com API key válida na Models tab
  2. NÃO configurar Deepgram
  3. Ir para `/onboarding` → mostra "Provider pendente", "Configurar Deepgram", "Valide a chave Deepgram"
- **Impacto:** Usuário que escolheu OpenRouter/Gemini/Groq/AssemblyAI como provider principal vê onboarding quebrado, com passos errados e CTAs apontando para Deepgram. Bug reportado no staging (BUG A do prompt).
- **Causa provável:** A rota de onboarding (linha 365-426) foi escrita antes do registry de providers e nunca foi atualizada. A função `_primary_ready` (linha 251-255) existe e está correta, mas NÃO é usada no onboarding.
- **Correção sugerida:**
  1. Trocar `provider_ready = (not status.deepgram_required) or deepgram_configured` por `provider_ready = _primary_ready(user.id, _model_settings_for(user.id))`
  2. Trocar provider_label para usar provider spec: `get_provider_spec(model_settings.primary_provider).label`
  3. Trocar CTA para apontar para `/models` em vez de `/settings/deepgram`
  4. Remover mensagens hardcoded de Deepgram dos passos
  5. Atualizar `job_service.py:55` — a flag `deepgram_required` passada em `run_once` é a mesma `app.state.transcription_status.deepgram_required`
- **Teste necessário:** `test_onboarding_shows_correct_provider_and_cta_for_openrouter`

### BUG-CRIT-002 — Dashboard mostra sempre "Deepgram", ignora provider selecionado

- **Severidade:** CRITICAL
- **Categoria:** UI / Dashboard
- **Arquivos:**
  - `app/web/templates/dashboard.html:7,37,42`
  - `app/web/templates/jobs.html:22,25`
- **Funções/linhas:**
  - `dashboard.html:7` — `"save your Deepgram key"` hardcoded
  - `dashboard.html:37` — `"Deepgram opcional, modelo local será usado"` hardcoded
  - `dashboard.html:42` — `"Deepgram (transcrição local desativada)"` hardcoded
  - `jobs.html:22` — `"Configure uma Deepgram API Key para iniciar uma transcrição"` hardcoded
  - `jobs.html:25` — `"Deepgram opcional"` hardcoded
- **Evidência:**
  - Apesar de o dashboard Python (linha 350-356) passar `provider_label` e `model_settings` corretamente para o template, o template ignora esses valores completamente no bloco "Transcription" (linhas 33-43).
  - O bloco "Transcription" usa `transcription_status.local_valid` (config LOCAL de ambiente), em vez de `provider_ready` ou `model_settings.primary_provider`.
- **Como reproduzir:** Configurar qualquer provider não-Deepgram na Models tab, ir ao Dashboard.
- **Impacto:** Usuário vê "Deepgram" quando tem OpenRouter configurado. Bug reportado (BUG B do prompt).
- **Correção sugerida:**
  1. `dashboard.html:7` — usar `provider_label` + `model_settings.primary_model`
  2. Bloco "Transcription" (linhas 33-43) deve checar `provider_ready` e `provider_label` em vez de `transcription_status.*`
  3. `jobs.html:22,25` — mesmo
- **Teste necessário:** `test_dashboard_shows_provider_label_not_deepgram_when_openrouter_configured`

### BUG-CRIT-003 — `RUN_ONCE_MESSAGES` e `create_next_pending_job` hardcodam "Deepgram"

- **Severidade:** CRITICAL
- **Categoria:** UI messages / Job service
- **Arquivos:**
  - `app/web/main.py:85` — `no_deepgram_key` message
  - `app/services/job_service.py:56` — `return JobCreationResult("no_deepgram_key")`
- **Funções/linhas:**
  - `main.py:85` — `"no_deepgram_key": "Configure sua Deepgram API Key antes de iniciar uma transcrição."`
  - `job_service.py:55-56` — `if deepgram_required and not _has_provider_credential(settings): return JobCreationResult("no_deepgram_key")`
- **Evidência:**
  - A mensagem de erro "no_deepgram_key" menciona Deepgram explicitamente, mesmo quando o usuário tem outro provider configurado mas sem chave.
  - O status "no_deepgram_key" é semanticamente errado: o problema é falta de credential para QUALQUER provider.
  - `_has_provider_credential` (linha 27-32) já verifica corretamente se há qualquer credential. O problema é só a mensagem.
- **Impacto:** Mensagem confusa quando o problema é falta de chave OpenRouter/Gemini, não de Deepgram.
- **Correção sugerida:** Renomear status para `"no_provider_key"` e mensagem para algo genérico como "Configure a chave do seu provedor na aba Models antes de iniciar."
- **Teste necessário:** Atualizar `test_job_service.py::test_reports_no_deepgram_key` para usar mensagem genérica

---

## 5. High findings

### BUG-HIGH-004 — Env vars de compressão ausentes do `.env.example` e `docker-compose.yml`

- **Severidade:** HIGH
- **Arquivos:**
  - `app/audio/config.py:110-116` — lê env vars não documentadas
  - `.env.example` — não contém `AUDIO_COMPRESSION_ENABLED`, `AUDIO_CLOUD_CHUNK_TARGET_MB`, `AUDIO_PROVIDER_LIMIT_DEFAULT_MB`, `OPENROUTER_MAX_UPLOAD_MB`, `GEMINI_MAX_FILE_API_MB`, `AUDIO_COMPRESSION_TARGET_MB`
  - `docker-compose.yml` — mesmo problema
- **Evidência:** `audio/config.py` lê 15+ env vars. Apenas `AUDIO_PREPROCESSING_ENABLED`, `AUDIO_TARGET_SAMPLE_RATE`, `AUDIO_TARGET_CHANNELS`, `AUDIO_TARGET_BITRATE`, `AUDIO_CHUNK_MAX_DURATION_SECONDS`, `AUDIO_CHUNK_OVERLAP_SECONDS`, `AUDIO_MAX_INLINE_MB`, `AUDIO_MAX_FILE_API_MB` estão documentadas. As demais têm valores default no código mas o operador não sabe que existem.
- **Impacto:** Operador não consegue ajustar limites de upload por provider (OpenRouter 99MB → 50MB, etc). Os defaults são razoáveis mas invisíveis.
- **Correção sugerida:** Adicionar TODAS as env vars de `audio/config.py` ao `.env.example` e `docker-compose.yml` (x-transcription-env anchor).

### BUG-HIGH-005 — `prepare_audio_for_provider` chamado para local provider

- **Severidade:** HIGH
- **Arquivos:**
  - `app/worker/processor.py:166-169`
  - `app/audio/config.py:60-67`
- **Funções/linhas:**
  - `processor.py:166` — `capabilities = get_provider_capabilities(resolved.name, config)`
  - `processor.py:169` — `prepared = prepare_audio_for_provider(media_path, capabilities, job_dir, ...)`
- **Evidência:**
  - `get_provider_capabilities` retorna `max_upload_mb=999999` para providers desconhecidos/locais (linha 60-67 do config.py).
  - `prepare_audio_for_provider` sempre é chamado, inclusive para local providers.
  - Para local providers, o arquivo já é convertido para WAV em `extract_audio_to_wav` (dentro do provider local). A pré-compressão pode causar dupla conversão ou overhead desnecessário.
- **Impacto:** Overhead de processamento desnecessário para transcrição local. Possível conflito de formato (WAV convertido para FLAC e depois o provider local espera WAV).
- **Correção sugerida:** Pular `prepare_audio_for_provider` quando `resolved.kind == "local"`.

### BUG-HIGH-006 — Sem proteção CSRF nos formulários

- **Severidade:** HIGH
- **Arquivos:**
  - `app/web/main.py:148-153` — SessionMiddleware config
  - `app/web/templates/base.html` — sem CSRF tokens
  - `app/web/templates/models.html`, `jobs.html`, `admin_users.html`, etc. — forms sem CSRF
- **Evidência:** Apenas `same_site="lax"` no cookie de sessão. Nenhum token CSRF é gerado ou validado em forms POST. Formulários como `/models/provider`, `/models/credentials`, `/admin/users/*`, `/jobs/run-once` são vulneráveis a CSRF.
- **Impacto:** Um atacante pode forçar um admin logado a criar usuários, salvar credenciais, ou enfileirar jobs via CSRF.
- **Correção sugerida:** Adicionar middleware CSRF (ex: `starlette_csrf`) ou pelo menos tokens CSRF manuais nos forms.

### BUG-HIGH-007 — `AUDIO_COMPRESSION_ENABLED` não documentado, default é `True`

- **Severidade:** HIGH
- **Arquivos:**
  - `app/audio/config.py:110` — `compression_enabled=_bool(values, "AUDIO_COMPRESSION_ENABLED", True)`
  - `.env.example:123` — só tem `AUDIO_PREPROCESSING_ENABLED=false`
- **Evidência:** `compression_enabled` defaulta para `True`, mas `enabled` (preprocessing) defaulta para `False`. Se o operador ativar `AUDIO_PREPROCESSING_ENABLED=true`, a compressão é automaticamente ativada sem ser documentada. A env var `AUDIO_COMPRESSION_ENABLED` existe no código mas NÃO aparece no `.env.example` nem docker-compose.
- **Impacto:** Ativar preprocessing também ativa compressão sem o operador saber. Se o ffmpeg não estiver disponível, jobs podem falhar inesperadamente.

---

## 6. Medium findings

### BUG-MED-008 — `run_once` e `check_now` usam `deepgram_required` do estado global de transcrição

- **Severidade:** MEDIUM
- **Arquivos:**
  - `app/web/main.py:739` — `deepgram_required=app.state.transcription_status.deepgram_required`
  - `app/web/main.py:633` — mesmo em check_now
- **Evidência:** Ambas as rotas passam o flag `deepgram_required` baseado no estado de transcrição local do ambiente. Se o usuário tem um provider cloud (ex: OpenRouter) com key salva, `deepgram_required` ainda é `True` porque a config local está disabled. O job_service usa `deepgram_required` para bloquear criação de job (linha 55). Mas o `_has_provider_credential` verifica corretamente — então o job PODE ser criado. O bug é apenas semântico/conceitual.
- **Impacto:** Fluxo funciona, mas a arquitetura está inconsistente: `deepgram_required` como flag não reflete a realidade do registry.
- **Correção sugerida:** Renomear `deepgram_required` para `provider_credential_required` ou refatorar para usar o registry.

### BUG-MED-009 — Provider "local" listado em `SELECTABLE_PROVIDERS` mas não pode ser selecionado

- **Severidade:** MEDIUM
- **Arquivos:**
  - `app/transcription/provider_models.py:197` — `SELECTABLE_PROVIDERS = CLOUD_PROVIDERS`
  - `app/transcription/provider_models.py:194` — `CLOUD_PROVIDERS = (DEEPGRAM, OPENROUTER, GEMINI, GROQ, ASSEMBLYAI)`
- **Evidência:** `LOCAL` não está em `SELECTABLE_PROVIDERS`. O `_providers_view` (main.py:236-249) itera sobre `SELECTABLE_PROVIDERS`, então "local" NÃO aparece na Models tab. Isso está correto pelo design (local é controlado por env), mas a documentação e `PROVIDERS` dict incluem `LOCAL`, o que pode confundir.
- **Impacto:** Baixo. Mas inconsistente com a spec que diz que `LOCAL` deveria aparecer na UI "para docs".

### BUG-MED-010 — Dockerfile não instala ffmpeg quando `AUDIO_PREPROCESSING_ENABLED=true` sem `INSTALL_LOCAL_TRANSCRIPTION`

- **Severidade:** MEDIUM
- **Arquivos:** `Dockerfile:36-50`
- **Evidência:** O ffmpeg só é instalado via build args `INSTALL_WHISPER_CPP=true`, `INSTALL_LOCAL_TRANSCRIPTION=true`, ou `INSTALL_PYANNOTE` indiretamente. Se o operador quiser usar APENAS audio preprocessing (sem transcrição local), precisa de ffmpeg mas o Dockerfile não cobre esse caso.
- **Impacto:** `prepare_audio_for_provider` falha com `FfmpegNotFoundError` em imagem padrão. A mensagem é amigável, mas o operador não sabe como resolver sem ler o Dockerfile.
- **Correção sugerida:** Adicionar build arg `INSTALL_FFMPEG` ou documentar.

### BUG-MED-011 — `RUN_ONCE_MESSAGES["no_deepgram_key"]` contém texto de chave Deepgram

- **Severidade:** MEDIUM (overlap com BUG-CRIT-003)
- **Arquivos:** `app/web/main.py:85`
- **Evidência:** Já documentado em BUG-CRIT-003.

### BUG-MED-012 — `GROQ_MAX_UPLOAD_MB` documentado mas não no `.env.example`

- **Severidade:** MEDIUM
- **Arquivos:** `.env.example`, `documentation/39-groq-provider.md:37`
- **Evidência:** A documentação do Groq menciona `GROQ_MAX_UPLOAD_MB` e `GROQ_USE_DEV_LIMIT`, mas essas env vars não estão no `.env.example`.
- **Impacto:** Operador não descobre esses knobs sem ler a doc completa.

### BUG-MED-013 — `assemblyai` como provider: sem env var de limite de tamanho

- **Severidade:** MEDIUM
- **Arquivos:** `app/audio/config.py:53-59`
- **Evidência:** `get_provider_capabilities` para assemblyai usa `config.provider_limit_default_mb` (default 99) mas não tem env var específica. AssemblyAI aceita uploads maiores (até 5h de áudio). O limite de 99MB pode ser muito restritivo.
- **Impacto:** Arquivos legítimos para AssemblyAI podem ser rejeitados desnecessariamente.

---

## 7. Low findings

### BUG-LOW-014 — Mix de idiomas nos templates (en/pt-BR)

- **Severidade:** LOW
- **Arquivos:** `dashboard.html:6-7`, `dashboard.html:16`, `models.html:7`, `jobs.html:29`, `base.html:23`
- **Evidência:** Dashboard usa "Meeting transcription control panel" (en) e "Connect Google Drive" (en) misturado com "Deepgram opcional" (pt). Models usa "Status: Configurado" (pt). Jobs usa "After starting a job, refresh..." (en). Logout usa "Logout" (en).
- **Impacto:** UX inconsistente mas funcional.
- **Correção sugerida:** Padronizar para pt-BR ou en, não ambos.

### BUG-LOW-015 — `_http_error_context` ignora `detail` em erros 503

- **Severidade:** LOW
- **Arquivos:** `app/web/main.py:1118-1133`
- **Evidência:** A função `_http_error_context` mapeia status codes para mensagens fixas. O `detail` original (que pode conter info útil como "database unreachable" ou "worker repositories unavailable") é descartado. Isso é por design de segurança (não vazar internals), mas pode dificultar debugging.
- **Impacto:** Mensagens de erro genéricas para o usuário.

### BUG-LOW-016 — `file_id` não usado em `normalized_payload` no chunk stitching

- **Severidade:** LOW
- **Arquivos:** `app/worker/processor.py:202-212`
- **Evidência:** Na branch chunked (linha 202-212), `file_id` não é passado para `normalized_payload` ou `render_transcript_text`. O campo `file_id` existe na assinatura mas não é usado.
- **Impacto:** Baixo. Transcript text pode não incluir o file_id esperado.

---

## 8. Info / cleanup

- A branch `qa/next-platform-features-v2` tem um arquivo `opencode.json` não rastreado (?? no git status).
- O arquivo `app/main.py` (legacy CLI) existe e deve ser preservado conforme regra #1.
- As migrations têm 4 arquivos mas apenas 1 head: `0001_create_initial_postgres_schema`, `0002_add_transcript_fulltext_index`, `0002_automation_and_retry`, `0002_provider_registry_tables`. As 3 "0002" são sequenciais (não branches), confirmado por `alembic branches` vazio. A nomeação com mesmo prefixo é confusa e pode causar problemas em deploys futuros.
- `app/worker/processor.py:479-618` contém o método `_preprocess_media_if_needed` que parece ser código legado não utilizado — o fluxo atual usa `prepare_audio_for_provider` (do `app/audio/compression.py`). O método `_preprocess_media_if_needed` NÃO é chamado em `process()`.

---

## 9. Provider readiness audit

| Provider | Key salva? | Modelo ativo? | Readiness correta? | UI correta? | Bugs |
|----------|-----------|---------------|-------------------|-------------|------|
| Deepgram | Configurável via /models | Selecionável via /models | SIM (legacy path) | SIM | Nenhum |
| OpenRouter | Configurável via /models | Selecionável via /models | NÃO (BUG-CRIT-001) | NÃO (BUG-CRIT-002) | Onboarding e dashboard hardcodam Deepgram |
| Gemini | Configurável via /models | Selecionável via /models | NÃO (BUG-CRIT-001) | NÃO (BUG-CRIT-002) | idem |
| Groq | Configurável via /models | Selecionável via /models | NÃO (BUG-CRIT-001) | NÃO (BUG-CRIT-002) | idem |
| AssemblyAI | Configurável via /models | Selecionável via /models | NÃO (BUG-CRIT-001) | NÃO (BUG-CRIT-002) | idem |
| Local | N/A (env-driven) | N/A | SIM | OK (mas mostra "Deepgram opcional") | Hardcoded Deepgram nos templates |

---

## 10. Worker / queue / audio audit

- **Worker é único lugar que transcreve:** SIM. Confirmado em `processor.py`. Nenhuma transcrição no web.
- **Web baixa arquivo pesado:** NÃO. Apenas o worker faz download via DriveClient.
- **Processor resolve provider corretamente:** SIM. `_resolve_provider` (linha 409-436) usa o registry para OpenRouter/Gemini/Groq/AssemblyAI e legacy para Deepgram/local.
- **Cloud/local classificação:** SIM. `classify_provider_kind` é usado.
- **Retry/backoff:** SIM. `_handle_failure` com `schedule_retry` e backoff exponencial.
- **Dead-letter:** SIM. `mark_failed` + `queue.mark_dead`.
- **Webhook:** SIM. Dispara em completed/failed terminal.
- **Stacktrace fica só em log:** SIM. `user_message` é usado na UI, exc no log.
- **Job sem áudio:** SIM. `NoAudioTrackError` amigável.
- **Job sem provider:** SIM. `ProviderCredentialMissingError` amigável.
- **Redis idle timeout:** Corrigido. `test_worker_queue_loop.py::test_run_queue_loop_survives_idle_timeout` confirma.
- **Cloud semaphore:** SIM. Lua script atômico.
- **Local lock:** SIM. Token-checked.
- **Compression/chunking:** SIM. Fluxo `prepare_audio_for_provider` implementado. Testes cobrem OpenRouter e Groq oversize.

---

## 11. Database / migration audit

- **1 head:** SIM. `0002_transcript_fts`.
- **Migrations lineares:** SIM. Sem branches.
- **Migrate antes de web/worker:** SIM. `depends_on migrate service_completed_successfully`.
- **Models batem com migrations:** PARCIAL. As 3 migrations 0002_* têm nomes confusos (mesmo prefixo). Verificar `tests/test_models_metadata.py` — todas passam.
- **Índices:** SIM. Fulltext index em transcripts, unique partial index em completed jobs, indexes em retry/created.
- **Repository memory e postgres mesmo contrato:** SIM. Testes passam.

---

## 12. Security audit

- **Senha hashada:** SIM. `hash_password` + `verify_password`.
- **Session cookie:** `same_site="lax"`, `https_only` configurável. Sem `secure` flag por padrão (SESSION_COOKIE_SECURE=false).
- **CSRF:** NÃO. Sem proteção CSRF nos forms. Risco HIGH (BUG-HIGH-006).
- **Admin routes protegidas:** SIM. `require_admin` dependency.
- **Jobs owner-scoped:** SIM. Verificação `job.user_id != user.id`.
- **Download/export owner-scoped:** SIM. `get_transcript_export` com user_id.
- **Search owner-scoped:** SIM. `search_transcripts(user_id, ...)`.
- **Extension upload token:** SIM. `secrets.compare_digest` com constant-time.
- **Upload size cap:** SIM. Middleware + handler check.
- **XSS:** Baixo risco. Jinja2 com autoescape padrão.
- **Webhook não vaza segredo:** SIM. `redact` nas funções de log.
- **Logs não vazam API key:** SIM. `observability.redact` cobre "key", "secret", "token", "password".
- **Open redirect:** OAuth state validation presente. Confirmado.
- **SSRF:** Provider APIs são chamadas com URLs fixas. Sem input de URL do usuário.
- **Rate limit web routes:** NÃO. Sem rate limiting nas rotas web. Risco MEDIUM.

---

## 13. UI functional audit

- **Links quebrados:** NÃO encontrados.
- **Forms com action errada:** NÃO encontrados.
- **Botões que não fazem nada:** NÃO encontrados.
- **Mensagem incoerente:** SIM. (BUG-CRIT-001, BUG-CRIT-002).
- **Mistura idioma:** SIM. (BUG-LOW-014).
- **Status conflitante:** SIM. Dashboard mostra "Deepgram (transcrição local desativada)" quando provider é OpenRouter.
- **Select provider/modelo:** Funciona corretamente na Models tab.
- **Test button:** Funciona. Usa `verify_provider_key`.
- **Admin role select:** OK.
- **Jobs failed sem erro visível:** O erro é visível na página de detalhe do job (`job_detail.html`).
- **Retry:** Funciona. `reset_job_for_retry` + remove da dead letter + enqueue.
- **Run once vs Verificar agora:** "Run once" escaneia pasta drive. "Verificar agora" é o auto-poll manual.

---

## 14. Docker / Dokploy audit

- **5 serviços:** SIM. postgres, redis, migrate, web, worker.
- **migrate com restart no:** SIM (`restart: "no"`).
- **web/worker dependem de migrate:** SIM (`depends_on migrate service_completed_successfully`).
- **Healthchecks:** OK. postgres `pg_isready`, redis `redis-cli ping`, web `curl /health`.
- **WEB_PORT configurável:** SIM. `--port` no command do web.
- **GOOGLE_REDIRECT_URI:** `http://localhost:8000/oauth/google/callback` (deve ser sobrescrito em staging/prod).
- **APP_SECRET_KEY:** Obrigatório mas default vazio no migrate (BUG?). No migrate, `APP_SECRET_KEY: ""`.
- **SESSION_COOKIE_SECURE:** false por padrão.
- **Build args:** OK. Local models são opcionais.
- **ffmpeg quando compression enabled:** NÃO garantido (BUG-MED-010).
- **Volumes:** postgres_data, redis_data persistidos. tmp e models como bind mounts.
- **GH Actions:** Não verificado nesta análise.

---

## 15. Test coverage gaps

Testes que NÃO existem (deveriam existir):

| Teste | Cobertura |
|-------|-----------|
| OpenRouter configurado → onboarding mostra "Configurado" | NÃO |
| Dashboard com OpenRouter/Gemini → mostra provider correto | NÃO |
| `/ready` com provider registry configurado | PARCIAL (testa Postgres + queue apenas) |
| Onboarding CTA aponta para /models quando provider não-Deepgram | NÃO |
| CSRF protection em forms | NÃO |
| `AUDIO_COMPRESSION_ENABLED` com `AUDIO_PREPROCESSING_ENABLED` juntos | NÃO |
| Template dashboard renderiza `provider_label` corretamente | PARCIAL (test_web_ui.py cobre dashboard parcialmente) |
| `prepare_audio_for_provider` com local provider | NÃO |
| Migrations cobrem todas as env vars de audio config | NÃO |

---

## 16. Recommended fix plan

### Antes de merge (bloqueadores)

1. **BUG-CRIT-001:** Refatorar rota `/onboarding` para usar `_primary_ready` e provider spec do registry.
2. **BUG-CRIT-002:** Atualizar `dashboard.html` bloco Transcription para usar `provider_ready`/`provider_label`.
3. **BUG-CRIT-003:** Atualizar `RUN_ONCE_MESSAGES` e `job_service.py` para mensagens provider-agnostic.

### Pós-merge curto prazo

4. **BUG-HIGH-004:** Adicionar env vars de compressão ao `.env.example` + `docker-compose.yml`.
5. **BUG-HIGH-006:** Adicionar CSRF protection.
6. **BUG-HIGH-007:** Documentar `AUDIO_COMPRESSION_ENABLED` e revisar default.
7. **BUG-MED-010:** Adicionar `INSTALL_FFMPEG` build arg ou documentar no README.

### Futuro

8. Padronizar idioma nos templates (BUG-LOW-014).
9. Adicionar rate limiting nas rotas web.
10. Remover código legado `_preprocess_media_if_needed` em `processor.py`.

---

## 17. Final recommendation

- **Merge agora?** NÃO
- **Condições para merge:** Corrigir BUG-CRIT-001, BUG-CRIT-002, BUG-CRIT-003.
- **Próximo prompt recomendado:** "Corrija os bugs críticos do bug-hunt report e rode os testes."

## 18. Fix status

| Bug | Severity | Status | Notes |
|-----|----------|--------|-------|
| BUG-CRIT-001 | CRITICAL | FIXED | Created `app/web/provider_readiness.py`, onboarding uses `compute_provider_readiness` |
| BUG-CRIT-002 | CRITICAL | FIXED | Dashboard/jobs templates use `provider_readiness` and pt-BR text, no Deepgram hardcoded |
| BUG-CRIT-003 | CRITICAL | FIXED | `RUN_ONCE_MESSAGES` renamed `no_provider_key`, `job_service.py` returns `no_provider_key` |
| BUG-HIGH-004 | HIGH | FIXED | All audio env vars added to `.env.example` and `docker-compose.yml` |
| BUG-HIGH-005 | HIGH | FIXED | `processor.py` skips `prepare_audio_for_provider` when `resolved.kind == "local"` |
| BUG-HIGH-006 | HIGH | FIXED | CSRF module created (`app/web/csrf.py`), all form POST routes validated, templates include hidden `csrf_token` |
| BUG-HIGH-007 | HIGH | FIXED | `AUDIO_COMPRESSION_ENABLED` documented in `.env.example` |
| BUG-MED-008 | MEDIUM | FIXED | `run_once` uses provider-agnostic `deepgram_required` flag from app state; message is now `no_provider_key` |
| BUG-MED-010 | MEDIUM | FIXED | `INSTALL_FFMPEG=true` build arg added to Dockerfile (default on) |
| BUG-MED-012 | MEDIUM | FIXED | `GROQ_MAX_UPLOAD_MB` and `GROQ_USE_DEV_LIMIT` added to `.env.example` and compose |
| BUG-MED-013 | MEDIUM | FIXED | `ASSEMBLYAI_MAX_UPLOAD_MB` env var added, `assemblyai_max_upload_mb` field in `AudioConfig` |
| BUG-LOW-014 | LOW | FIXED | Templates standardized to pt-BR (dashboard, jobs, login, models, base, admin_users) |
| Cleanup | INFO | DEFERRED | `_preprocess_media_if_needed` dead code kept for future cleanup; not removed to avoid risk |
