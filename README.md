<div align="center">

# AUREM CTO Dev

### **ORA ships code. Directly to GitHub.**

Describe what you want. ORA reads your codebase, writes the code, runs security checks, and commits straight to your GitHub repo — no pull requests, no manual merges, no context switching.

[**Start free → auremcto.com**](https://auremcto.com/signup) · [**Live product**](https://auremcto.com) · [**Ship Wall**](https://auremcto.com/wall) · [**Wrapped**](https://auremcto.com/wrapped)

`455 tests passing` · `Direct GitHub commit` · `Flat fee — no token billing` · `Works on mobile` · `VS Code extension`

</div>

---

## Why AUREM exists

GitHub Copilot moved to usage-based token billing on June 1, 2026. Developers with heavy agent usage report monthly bills of **$750+** instead of the previous flat $29.

AUREM Pro is **$19/month, flat**. Ship 5 tasks or 500 tasks — the price is the same. No credit pools, no model-tier pricing, no surprises.

The four things AUREM does that no other tool combines:

1. **Direct GitHub commit** — code lands on `main` via the GitHub REST API. No PR, no human-in-the-loop.
2. **Project Brain** — per-repo permanent memory of your stack, decisions, and team preferences.
3. **F12 browser capture** — one `<script>` tag and every browser error auto-routes to Mode D debug.
4. **Web UI that works on mobile** — Cursor / Copilot / Claude Code all need a desktop IDE.

---

## How it works

```
  💬                  🔍                  ✍️                  🚀
  ───                 ───                 ───                 ───
  Describe       →   ORA reads      →   Writes &       →   Direct
  the task            your codebase      validates           GitHub commit

  "Add rate limiting   Searches relevant   Generates code,     Code committed via
   to /login — 5/min   files, understands  syntax-checks,      REST API. No PR.
   per IP"             stack & history     Vanguard scan       Repo is updated.
```

Live tape inside the chat shows every step in real time:

```
12:34:05  ⟳  Reading repository files…
12:34:07  ⟳  Found 12 related files. ORA thinking…
12:34:12  ⟳  Writing 3/4 files
12:34:14  ⟳  Linter check passed. Committing…
12:34:16  ✓  Done — commit a3f2b1c
```

---

## Getting started — 2 minutes to first commit

### 1. Sign up with GitHub
Go to **auremcto.com** → *Continue with GitHub* → authorize AUREM. No password needed.

### 2. Connect a repo
**Projects → Add Project** → paste any GitHub URL.

```bash
# Both formats work
https://github.com/username/my-startup
github.com/username/my-startup
```

### 3. Describe your task
Open **Dashboard** and type in the chat. Be specific — name the file and function when you can.

```text
✓ "Add rate limiting to /login in backend/routers/auth.py — max 5/min per IP"
✓ "Fix the 422 on POST /api/auth — Accept header is missing"
✓ "Add a dark-mode toggle to components/Navbar.jsx"
✓ "Audit the entire codebase for security vulnerabilities"
```

### 4. Verify on GitHub
Click the commit SHA in the chat → GitHub opens the diff. Done.

---

## ORA's 6 modes

ORA auto-detects intent. You don't have to say *use mode C* — just type naturally.

| Mode | Name | When it fires | Example |
| :--- | :--- | :--- | :--- |
| **A** | Chat | Quick answers, no code changes | *"What does the auth middleware do?"* |
| **B** | Advice | Architecture decisions, comparisons | *"Redis or MongoDB for sessions?"* |
| **C ⭐** | **Code ship** | Writes code + commits to GitHub (~70% of tasks) | *"Add Stripe webhook handler"* |
| **D** | Debug | Diagnoses + fixes bugs (pairs with F12 capture) | *"Login returns 422"* |
| **E** | Audit | Full security scan of the repo | *"Audit for exposed secrets"* |
| **F** | Engage | Market research, copy, product feedback | *"How does my landing compare?"* |

### F12 Debug capture

Add one line to your site's `<head>`. Browser errors auto-route to Mode D — no typing.

```html
<script src="https://auremcto.com/F12ErrorCapture.js"></script>
```

Captures: console errors · network failures · stack traces · JavaScript exceptions.

---

## All features

### 🧠 Project Brain — permanent per-repo memory

ORA remembers everything about every project you connect.

**Stored**

- Tech stack and dependencies
- Team decisions (*"we decided no Redux"*)
- Team preferences (*"always use Tailwind"*)
- Last 5 GitHub commit messages
- Past task patterns and outcomes

**Debug surfaces**

- `/admin/brain/:projectId` — Brain Dump (see what ORA knows)
- Brain Replay — ask without committing
- *Show diff →* — click any past commit, render the diff in chat

### 🔒 Vanguard security scanner

Every commit scanned before it reaches GitHub. 25+ patterns checked in real time.

| Blocks from committing | Injects into ORA's context |
| :--- | :--- |
| AWS access keys | API security best practices |
| GitHub tokens accidentally committed | Auth implementation patterns |
| Stripe live keys in source | PCI compliance (Stripe tasks) |
| Database connection strings | GDPR / privacy-by-design |
| Python `SyntaxError` (`ast.parse`) | Frontend security guidelines |
| JS/TS errors (esbuild) | Backend hardening |

### ⚡ Two-agent Maxx mode

Enable Maxx in the Ship dialog for important tasks.

**DeepSeek V3** generates → **Claude Sonnet** reviews → then commits.

Two AI engineers on every task. Claude catches what DeepSeek misses: wrong imports, logic errors, security gaps. Available on **Pro** and **Team**.

### 🔀 Parallel agents

Large tasks spanning multiple parts of the codebase automatically split into **3 simultaneous agents**.

| Agent | Owns |
| :--- | :--- |
| ⚙️ Backend | FastAPI routes, services, models, database |
| 🎨 Frontend | React components, CSS, hooks, state |
| 🧪 Tests | pytest, env vars, README updates |

### 🌐 Live preview pane

When ORA ships frontend code (React, HTML, CSS, JS), Dashboard auto-splits into chat on the left + a live preview on the right. Iframe-blob rendering — no deploy, no waiting.

Click **◈ Preview** in the top bar to toggle. Drag the divider to resize. State persists across sessions.

### 🤖 Automations

Trigger tasks without any manual action.

**Setup — 3 steps**

1. Sidebar → **Automations** → *New automation*
2. Pick template variables for your task prompt
3. Copy the webhook URL into your GitHub repo → **Settings → Webhooks**

```text
# Template variables available
{branch}            # branch that was pushed
{pusher}            # GitHub username who pushed
{commit_count}      # number of commits in push
{commit_messages}   # list of commit messages
{repo}              # owner/repo-name
```

Every push to your repo now spins up a task automatically.

### 📊 ORA Wrapped — monthly stats

A Spotify-style card for your coding activity. Visit **/wrapped**.

| Tasks shipped | Time saved | Repos touched | Day streak |
| :---: | :---: | :---: | :---: |
| 23 | ~17h | 4 | 7 |

One click to share on X or LinkedIn.

### 🏆 Ship Wall — public feed

Every task ORA ships shows up at **auremcto.com/wall** — your name, repo, what was built, commit SHA, time ago. Drop the badge in your repo's README:

```markdown
[![Built with AUREM](https://auremcto.com/api/aurem-dev/wall/badge/your-username)](https://auremcto.com/wall)
```

### 🔧 VS Code extension

| Feature | What it does |
| :--- | :--- |
| ORA Chat sidebar | Full chat panel inside VS Code |
| Right-click Ship | Select code → *Ship via AUREM CTO* |
| Status bar pill | `🚀 ORA` when connected |
| GitHub OAuth | Same one-click login |

Install:

```bash
# Extensions panel → search "AUREM CTO" → Install
# Or via CLI:
ext install auremcto.aurem-cto
```

### 🔐 Security architecture

| Data protection | Code safety |
| :--- | :--- |
| GitHub PAT encrypted at rest (HKDF-Fernet) | Python AST parse before every commit |
| JWT tokens, 7-day expiry | JS/TS esbuild parse validation |
| Security headers on every response | Vanguard 007 — 25+ patterns |
| CORS restricted to auremcto.com | Auto-retry with error feedback |
| Rate limit: 30/min chat, 10/min tasks | Full rollback endpoint available |

---

## Honest comparison

> June 2026 data. No marketing claims — only verifiable features.

| Feature | **AUREM CTO** | Cursor 3 | Copilot | Claude Code | Devin |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Direct GitHub commit (no PR) | ✅ **only AUREM** | ❌ PR only | ❌ PR only | ❌ git CLI | ❌ PR only |
| Per-repo memory | ✅ Project Brain | ❌ | ❌ | ❌ | ⚠ Devin Wiki |
| F12 browser error capture | ✅ | ❌ | ❌ | ❌ | ✅ Chromium |
| Web UI (no IDE needed) | ✅ **+ mobile** | ❌ VS Code | ❌ | ❌ terminal | ❌ |
| Flat-fee pricing | ✅ $19/mo unlimited | ❌ credit pool | ❌ token billing | ✅ plan limits | ❌ per-task |
| Security scanner pre-commit | ✅ 25+ patterns | ❌ | ❌ | ❌ | ❌ |
| Two-agent code review | ✅ Maxx mode | ❌ | ❌ | ❌ | ❌ |
| Live preview pane | ✅ iframe blob | ❌ | ❌ | ❌ | ❌ |
| Parallel agents | ✅ 3 domains | ✅ 8 agents | ❌ | ❌ | ❌ |
| Webhook-triggered automations | ✅ Automations | ✅ | ❌ | ❌ | ❌ |
| Git commit history learning | ✅ `get_commit_diff` | ❌ | ❌ | ❌ | ✅ memory |
| Entry paid price | **$9/mo** | $20/mo | $10/mo\* | $17/mo | $20/mo |

\* *GitHub Copilot switched to usage-based token billing on June 1, 2026. Devs with heavy agent usage reporting $750+ monthly vs the previous $29 flat fee.*

**The combination that doesn't exist anywhere else:** direct GitHub commit **+** Project Brain memory **+** F12 capture **+** mobile-capable web UI.

---

## Pricing

Flat fee. No token surprises. No credit pools. Cancel anytime from Settings.

| | **Free** | **Starter** | **Pro** ⭐ | **Team** |
| :--- | :--- | :--- | :--- | :--- |
| **Price** | $0 | **$9 / mo** | **$19 / mo** | **$35 / user / mo** |
| Tasks / month | 10 | 50 | Unlimited | Unlimited per user |
| Standard ship mode | ✅ | ✅ | ✅ | ✅ |
| Project Brain | — | ✅ | ✅ | ✅ |
| Maxx mode (Claude review) | — | — | ✅ | ✅ |
| Parallel agents | — | — | ✅ | ✅ |
| Live preview pane | — | — | ✅ | ✅ |
| Automations | — | — | ✅ | ✅ |
| Priority queue | — | — | — | ✅ |
| Team admin panel | — | — | — | ✅ |
| Support | community | email | priority | dedicated |

### Cost transparency

```text
AUREM's cost per task                Why Pro is profitable at $19
─────────────────────────            ──────────────────────────────
DeepSeek V3 generation ~$0.02–0.04   Avg dev ships 20–50 tasks/mo
Claude review (Maxx)   ~$0.01–0.02   Break-even: 300–600 tasks/mo
Total per task         ~$0.03–0.06   At 200 tasks → still profitable
```

---

## Quick start

```bash
# 1. Create free account — 10 tasks/month, no card
https://auremcto.com/signup

# 2. Install the VS Code extension
ext install auremcto.aurem-cto
```

```html
<!-- 3. Add F12 capture to your site -->
<script src="https://auremcto.com/F12ErrorCapture.js"></script>
```

---

## Tips for best results

- **Be specific about files.** *"Fix login"* is vague. *"Fix `/api/auth/login` in `backend/routers/auth.py`"* is precise.
- **Use F12 capture for bugs.** Drop the `<script>` in your `<head>` and browser errors arrive in ORA on their own.
- **Enable Maxx for critical code.** Auth, payments, database migrations — let Claude review DeepSeek's work.
- **Build Brain context early.** Tell ORA your tech-stack decisions once. It remembers forever.

---

## Useful links

| Product | Admin (after login) | Contact |
| :--- | :--- | :--- |
| → [auremcto.com](https://auremcto.com) | → `/admin/overview` — system health | → support@auremcto.com |
| → [/wall](https://auremcto.com/wall) — ship feed | → `/admin/architecture` — code map | → Settings → Support |
| → [/wrapped](https://auremcto.com/wrapped) — your stats | → `/admin/brain/:projectId` — brain debug | → GitHub Issues for bugs |
| → [/signup](https://auremcto.com/signup) | → `/automations` — webhook triggers | |

---

<div align="center">

**Built by developers, for developers who'd rather ship than fight their tools.**

</div>
