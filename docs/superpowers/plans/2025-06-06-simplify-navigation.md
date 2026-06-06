# Simplify Navigation and Onboarding Journey

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the information architecture to reduce clicks and simplify the product journey for common users.

**Architecture:** Collapse top navigation from 9 items to 5; embed onboarding into the dashboard as a conditional checklist card; group all configuration under a single "Configurações" page with internal cards; move admin/observability links behind an admin-only "Sistema" section; keep all legacy routes working or redirecting.

**Tech Stack:** FastAPI, Jinja2, server-rendered HTML/CSS, PostgreSQL, Redis.

---

### Task 1: Simplify Base Navigation (`base.html` + `styles.css`)

**Files:**
- Modify: `app/web/templates/base.html`
- Modify: `app/web/static/styles.css`

**Changes:**
- Replace current nav groups with: `Transcrições`, `Modelos`, `Extensão`, `Configurações`, `Sair`.
- Remove `Onboarding`, `Buscar`, `Drive`, `Automação` from main nav.
- Add admin-only section: `Usuários`, `Sistema` (if `user.role == "admin"`).
- `Sistema` dropdown/link goes to `/admin/system` landing page.
- Add mobile hamburger toggle (`#navToggle`) that shows/hides `#mainNav` on small screens.
- Keep active states.

**CSS:**
- Add `.nav-toggle` button (hidden on desktop, visible < 768px).
- Adjust `.nav` to `display: none` on mobile by default, `display: flex` when `.is-visible`.
- Admin links styled subtly.

---

### Task 2: Embed Onboarding into Dashboard (`dashboard.html`)

**Files:**
- Modify: `app/web/templates/dashboard.html`
- Modify: `app/web/main.py` (pass onboarding state to dashboard context)

**Changes:**
- If provider not ready or extension not ready, show a prominent **"Falta pouco para começar"** card with a 4-step checklist:
  1. Instale a extensão → link `/extensao`
  2. Gere token → link `/extensao`
  3. Configure provider → link `/models`
  4. Grave uma reunião → link `/extensao` (or hint)
- If everything ready, show a compact "Tudo pronto" status and focus on recent jobs/stats.
- Keep recent jobs table.
- Remove technical wall of stats (Google, Drive origem, Modelos, Transcrição, Fila) or collapse them into a smaller "Status do sistema" card at the bottom.

**Backend:**
- In `dashboard` route, compute `onboarding_steps` and `needs_onboarding` similar to `/onboarding` logic.

---

### Task 3: Group Settings Page (`settings.html`)

**Files:**
- Modify: `app/web/templates/settings.html`

**Changes:**
- Expand `/settings` (linked as `Configurações` in nav) to show cards grid:
  - **Conta** — placeholder (password change, etc.)
  - **Extensão** → `/extensao`
  - **Google Drive** → `/settings/drive`
  - **Automação** → `/settings/automation`
  - **Providers / Modelos** → `/models`
  - **Admin / Sistema** → `/admin/system` (admin only)
- Keep warm, card-based layout consistent with dashboard.

---

### Task 4: Admin System Landing Page (`admin_system.html`)

**Files:**
- Create: `app/web/templates/admin_system.html`
- Modify: `app/web/main.py`

**Changes:**
- New route `GET /admin/system` (admin only).
- Cards linking to:
  - **Fila** → `/admin/queue`
  - **Saúde** → `/health` and `/ready`
  - **Usuários** → `/admin/users`
  - **Logs / Instruções** → placeholder
- Template extends `base.html` with `active_nav="admin_system"`.

---

### Task 5: Integrate Search into Jobs (`jobs.html`)

**Files:**
- Modify: `app/web/templates/jobs.html`

**Changes:**
- Add a search bar at the top of the jobs list that submits to `/search?q=...`.
- This removes the need for a top-level "Buscar" nav item while keeping search accessible.

---

### Task 6: Update Routes and Redirects (`main.py`)

**Files:**
- Modify: `app/web/main.py`

**Changes:**
- Add `GET /configuracoes` → render `settings.html` with `active_nav="settings"`.
- Add `GET /admin/system` → render `admin_system.html` with `active_nav="admin_system"`.
- Update `dashboard` route context to include onboarding readiness (`needs_onboarding`, `onboarding_steps`).
- Ensure `/onboarding`, `/jobs`, `/search` still work (no breaking changes).
- Update `active_nav` values where needed.

---

### Task 7: Tests

**Files:**
- Modify: `tests/test_web_ui.py`
- Modify: `tests/test_web_routes.py`

**New tests / updates:**
- Navbar does NOT render "Onboarding" as a primary nav item.
- Common user does NOT see "Fila" or "Admin" in top nav.
- Admin sees "Usuários" and "Sistema" links.
- Dashboard renders onboarding checklist when provider/extension not ready.
- Dashboard does NOT require Google Drive when extension/provider are ready.
- `/configuracoes` renders grouped settings cards.
- `/admin/system` renders admin landing cards.
- Mobile menu toggle exists in DOM.
- Legacy routes (`/onboarding`, `/jobs`, `/search`) still 200.

---

### Task 8: Validations and Commit

Run:
```bash
git status
.venv/bin/python -m pytest -v
.venv/bin/python -m pytest tests/e2e -v
.venv/bin/python -m compileall app scripts
docker compose config
docker compose build
```

Commit:
```bash
git add .
git commit -m "simplify navigation and onboarding journey"
git push
```
