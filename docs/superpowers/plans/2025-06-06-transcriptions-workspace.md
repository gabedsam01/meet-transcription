# Combine Jobs + Search into Transcriptions Workspace

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify `/jobs` and `/search` into a single `/transcricoes` workspace with search, list, detail, export, retry, and a compact queue panel.

**Architecture:** Server-rendered Jinja2 template with a desktop 70/30 split (main content + queue sidebar) and mobile tabbed layout. `/jobs` and `/search` redirect to `/transcricoes`. The existing `/jobs/{id}` detail page stays unchanged but back-links update.

**Tech Stack:** FastAPI, Jinja2, server-rendered HTML/CSS, PostgreSQL.

---

### Task 1: Create Transcriptions Template (`transcriptions.html`)

**Files:**
- Create: `app/web/templates/transcriptions.html`

**Layout:**
- Desktop: `.workspace` flex row with `.workspace-main` (flex: 1, min-width 0) and `.workspace-side` (width 280px).
- Mobile: tabs `[Transcrições] [Fila]` switching visibility via CSS.

**Sections:**
1. **Header** — eyebrow "Transcrições", title, subtitle. Actions: "Verificar agora", "Rodar agora".
2. **Search bar** — `<form method="get" action="/transcricoes">` with input `name="q"`, placeholder "Buscar por palavra-chave, cliente, reunião...". If `query` is set, show "X resultados para '...'".
3. **Alerts** — queue unavailable, transcription status, flash messages, backend error (same as jobs.html).
4. **Job list** — cards (not table) showing:
   - Name/date row
   - Status badge
   - If completed: export links (TXT, JSON, SRT, VTT, MD) + "Detalhes"
   - If failed: "Tentar novamente" button + "Detalhes"
   - If pending/processing: just status + "Detalhes"
   - Clicking card opens detail inline (or link to `/jobs/{{ job.id }}` if simpler).
5. **Queue panel (side)** — card with:
   - "Fila" title
   - Pending count, Processing count
   - Queue status badge (online/offline/polling)
   - If admin: link to `/admin/system`
6. **Empty states** — no jobs yet, no search results.

---

### Task 2: Update Routes in `main.py`

**Files:**
- Modify: `app/web/main.py`

**Changes:**
- Add `GET /transcricoes` — same logic as current `/jobs` but also handles `q` query param for search. If `q` is present, perform search via `worker_repos.transcripts.search_transcripts(user.id, query, limit=25)` and pass `results` and `query` to template. If no `q`, pass `jobs` list (same as `/jobs`).
- Update `GET /jobs` — redirect 303 to `/transcricoes` (keep the route for backward compatibility, but redirect).
- Update `GET /search` — if `q` present, redirect 303 to `/transcricoes?q=...`. If no `q`, redirect to `/transcricoes`.
- Keep `GET /jobs/{job_id}` detail page unchanged.
- Update `POST /jobs/run-once` and `/automation/check-now` redirect targets from `/jobs` to `/transcricoes`.
- Update `active_nav="jobs"` to `"transcriptions"` for the new route.

---

### Task 3: Update Base Navigation

**Files:**
- Modify: `app/web/templates/base.html`

**Changes:**
- Change nav link from `href="/jobs"` to `href="/transcricoes"`.
- Keep `active_nav == 'jobs'` check for backward compatibility, or update to `'transcriptions'`.

---

### Task 4: Update Job Detail Back Link

**Files:**
- Modify: `app/web/templates/job_detail.html`

**Changes:**
- Update back link from `href="/jobs"` to `href="/transcricoes"`.

---

### Task 5: Add CSS for Workspace Layout

**Files:**
- Modify: `app/web/static/styles.css`

**Add:**
- `.workspace { display: flex; gap: 1.5rem; align-items: flex-start; }`
- `.workspace-main { flex: 1; min-width: 0; }`
- `.workspace-side { width: 280px; flex-shrink: 0; }`
- `.workspace-tabs { display: none; }`
- `.job-card { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 1rem; background: var(--surface); }`
- `.job-card + .job-card { margin-top: .75rem; }`
- `.job-card-header { display: flex; justify-content: space-between; align-items: center; gap: 1rem; margin-bottom: .5rem; }`
- `.job-card-meta { font-size: .82rem; color: var(--muted); }`
- `.job-card-actions { display: flex; gap: .4rem; flex-wrap: wrap; margin-top: .5rem; }`

**Mobile (≤768px):**
- `.workspace { flex-direction: column; }`
- `.workspace-side { width: 100%; display: none; }`
- `.workspace-side.is-active { display: block; }`
- `.workspace-tabs { display: flex; gap: .25rem; margin-bottom: 1rem; }`
- `.workspace-tab { flex: 1; padding: .5rem; border: 1px solid var(--border); background: var(--surface-2); cursor: pointer; }`
- `.workspace-tab.is-active { background: var(--primary); color: #fff; border-color: var(--primary); }`

---

### Task 6: Update Tests

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_web_ui.py`
- Modify: `tests/test_web_local_transcription.py`
- Modify: `tests/e2e/*` if needed

**New/updated tests:**
- `/transcricoes` renders 200 for authenticated user.
- `/jobs` redirects 303 to `/transcricoes`.
- `/search?q=term` redirects 303 to `/transcricoes?q=term`.
- `/search` without `q` redirects to `/transcricoes`.
- Search on `/transcricoes?q=term` returns results scoped to user.
- Queue panel shows counts and status.
- Mobile tabs exist in DOM.
- Back-link on job detail points to `/transcricoes`.

---

### Task 7: Update Overview Doc

**Files:**
- Modify: `overview/qa-next-platform-features-v2.md`

**Add section** describing the unified Transcriptions workspace under the PR #7 changelog.

---

### Task 8: Validations and Commit

Run:
```bash
git status
.venv/bin/python -m pytest -v
.venv/bin/python -m compileall app scripts
docker compose config
docker compose build
```

Commit:
```bash
git add .
git commit -m "combine jobs search and queue into transcriptions workspace"
git push
```
