# UI/UX Audit — Kimi K2.6 + MCP Stitch

## 1. Executive Summary

- **Status geral:** A UI atual funciona tecnicamente, mas transmite uma impressão de "demo técnica" em vez de "SaaS profissional".
- **Pode mergear com UI atual?** SIM — mas com ressalvas. A UI atual não quebra funcionalmente, mas prejudica a percepção de qualidade.
- **Problemas bloqueadores:**
  1. Hardcode de Deepgram em múltiplas telas
  2. Mistura de idiomas (PT-BR/inglês) inconsistente
  3. Navegação sem hierarquia agrupada
  4. Mobile responsivo deficiente
- **Melhorias de alto impacto:**
  1. Design system unificado
  2. Redesign da navegação com agrupamento lógico
  3. Padronização de idioma para PT-BR
  4. Correção do hardcode de Deepgram
  5. Empty states e feedback visual melhorados

## 2. MCP Stitch Usage

- **Disponível?** SIM
- **Project ID:** projects/13826118074258041763
- **Screens gerados:** Dashboard (2560x2048), Models (2560x3566)
- **Design System:** Transcription Studio (assets/b80d957eba334b54a21037b99c72d09c)
- **Sugestões aproveitáveis:**
  - Paleta warm neutral (#f7f3ea) + deep green (#19735e)
  - Cards com sombras sutis
  - Tipografia Inter com hierarquia clara
  - Grid 12-colunas, max-width 1280px
  - Status badges pill
  - Navegação agrupada
  - Provider grid 2x3
  - Input focus glow
  - Botões 44px min-height
- **Sugestões descartadas:**
  - Dark mode
  - Gradientes em CTAs
  - Glassmorphism excessivo
  - Layouts assimétricos complexos

## 3. Screen Inventory

| Tela | Rota | Template | Prioridade |
|------|------|----------|------------|
| Dashboard | / | dashboard.html | Alta |
| Onboarding | /onboarding | onboarding.html | Alta |
| Models | /models | models.html | Alta |
| Jobs | /jobs | jobs.html | Alta |
| Job Detail | /jobs/{id} | job_detail.html | Alta |
| Search | /search | search.html | Média |
| Login | /login | login.html | Média |
| Settings | /settings | settings.html | Média |
| Drive Settings | /settings/drive | settings_drive.html | Média |
| Automation | /settings/automation | automation_settings.html | Média |
| Admin Users | /admin/users | admin_users.html | Média |
| Queue | /admin/queue | queue_status.html | Baixa |
| Error | — | error.html | Baixa |

## 4. Critical UX Bugs

### UX-CRIT-001 — Deepgram hardcoded no Dashboard
- **Tela:** Dashboard
- **Evidência:** "save your Deepgram key, and enqueue a run"
- **Impacto:** Alto — confunde usuários com outros providers
- **Correção:** Usar `provider_label` dinâmico

### UX-CRIT-002 — Deepgram hardcoded no Onboarding
- **Tela:** Onboarding
- **Evidência:** CTA "Configurar Deepgram" independente do provider
- **Impacto:** Alto — onboarding engana o usuário
- **Correção:** Usar `provider_label` dinâmico e rota `/models`

### UX-CRIT-003 — Deepgram hardcoded no Jobs
- **Tela:** Jobs
- **Evidência:** "Configure uma Deepgram API Key"
- **Impacto:** Alto — mensagem incorreta para provider local
- **Correção:** Mensagem condicional por provider

### UX-CRIT-004 — Mix de idiomas inconsistente
- **Tela:** Todas
- **Impacto:** Médio — quebra confiança
- **Correção:** Padronizar PT-BR

### UX-CRIT-005 — Job Detail sem transcript
- **Tela:** Job Detail
- **Impacto:** Alto — principal objetivo ausente
- **Correção:** Adicionar seção de transcript com tabs

## 5. Visual Design Issues

- Navbar apertada (10 links sem agrupamento)
- Selects nativos sem estilo
- Cards desalinhados (altura variável)
- Inputs sem focus state
- Botões inconsistentes (variantes confusas)
- Dashboard com muito vazio
- Logo sem identidade visual

## 6. Information Architecture Issues

- Onboarding não guia sequencialmente
- Models page com overload (6 providers em lista vertical)
- Search sem empty state/filtros
- Admin Users sem paginação/busca
- Queue sem visualização de métricas

## 7. Copy/Language Issues

- **Dashboard → Painel**
- **Jobs → Transcrições** (na navegação), manter "job" em contexto técnico
- **Search → Buscar**
- **Drive Settings → Drive**
- **Models → Modelos**
- **Run once → Rodar agora**
- **Check now → Verificar agora**
- Termos técnicos (provider, model, API key, fallback) mantidos em inglês

## 8. Accessibility Issues

- Contraste insuficiente em `.muted`
- Status badges sem aria-label
- Tabelas sem `scope="col"`
- Botões pequenos (button-sm ~28px)
- Focus states ausentes
- Tabelas com scroll sem indicação

## 9. Responsive/Mobile Issues

- Nav quebra desorganizada em mobile
- Cards sem ajuste de padding em mobile
- Tabelas sem indicador de scroll
- Botões abaixo de 44px em touch

## 10. Proposed Design System

### Paleta
```css
:root {
  --bg: #f7f3ea;
  --surface: #fffdf7;
  --text: #243029;
  --muted: #69756f;
  --primary: #19735e;
  --border: #e2d8c7;
  --danger: #b42318;
  --warning: #b7791f;
  --success: #157347;
}
```

### Tipografia
- Inter para todos os textos
- Headings semibold (600)
- Body regular (400)
- Labels medium (500)

### Componentes
- AppShell, Topbar, PageHeader, Card, StatCard, ProviderCard
- StatusBadge, ActionButton, FormField, Input, Select
- DataTable, Alert, EmptyState, TranscriptViewer

### Responsividade
- Desktop: 3 colunas cards, 2 colunas providers
- Tablet: 2 colunas
- Mobile: 1 coluna, nav hamburger, tabelas scrolláveis

## 11. Screen-by-Screen Redesign Proposal

### Base/Navbar
- Agrupar nav: Principal (Painel, Transcrições, Buscar), Configurações (Drive, Modelos, Automação), Admin (Usuários, Fila)
- Active state com border-bottom ou background
- Mobile: hamburger menu

### Login
- Card com branding
- Título: "Entrar"
- Alert box para erros

### Dashboard
- Header com eyebrow, title, CTA
- Stats cards (Google, Drive, Provider, Fila)
- Stats numéricos (Total, Em fila, Concluídos, Falhas)
- Recent jobs com badges
- Quick actions

### Onboarding
- Progress bar
- Steps com status visual
- Próximo step destacado
- CTA contextual

### Models
- Provider ativo card
- Selector card
- Provider grid 2x3
- Diarização info card

### Jobs
- Tabela com badges, provider, data
- Empty state
- Mobile: cards

### Job Detail
- Tabs: Transcrição, Tentativas, Metadados, Exportar
- Transcript viewer com speakers
- Export buttons

### Search
- Search card com input
- Result cards com snippet
- Empty state

### Automation
- Toggle card
- Config card
- Status card

### Admin Users
- Create card
- Table com role/status badges
- Actions dropdown

### Queue
- Metrics cards
- Jobs por status (cards/bars)
- Dead-letter list

### Error
- Icon + title + message + code
- CTA contextual

## 12. Implementation Plan

### Fase 1 — UX Funcional (Baixo risco)
- Corrigir hardcode Deepgram
- Padronizar idioma
- Arquivos: main.py, dashboard.html, onboarding.html, jobs.html, models.html

### Fase 2 — Design System (Médio risco)
- Refatorar styles.css
- Atualizar base.html
- Criar partials/

### Fase 3 — Telas Principais (Médio risco)
- Dashboard, Models, Onboarding, Jobs, Job Detail

### Fase 4 — Telas Secundárias (Baixo risco)
- Search, Automation, Admin, Queue, Error, Login, Settings

### Fase 5 — Mobile/A11y (Baixo risco)
- Media queries, keyboard nav, aria labels, contrast

## 13. Acceptance Criteria

- [ ] Todas telas renderizam sem erro
- [ ] Nav funciona desktop/mobile
- [ ] Active state visível
- [ ] Cards alinhados
- [ ] Badges com cores
- [ ] Tabelas scrolláveis
- [ ] Empty states presentes
- [ ] Labels associados a inputs
- [ ] Botões min-height 44px
- [ ] Focus states visíveis
- [ ] Contraste WCAG AA
- [ ] PT-BR nas labels
- [ ] Deepgram não hardcoded
- [ ] Tests passam
- [ ] Compile passa
- [ ] Docker build passa

## 14. Final Recommendation

**Merge com UI atual é aceitável, mas um PR de hotfix para corrigir o hardcode de Deepgram e padronizar o idioma deve ser prioritário.**

O redesign completo pode ser feito em um PR separado.

---

*Relatório gerado por Kimi K2.6 + MCP Stitch em 2026-06-06*
*Branch: qa/next-platform-features-v2*
*PR alvo: #7*
