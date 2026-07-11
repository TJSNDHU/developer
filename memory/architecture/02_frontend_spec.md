# 02 — FRONTEND LAYER (React SPA)
(Self-contained context module. System map: file 01.)

## STACK
React SPA on port 3000 (supervisor-managed, hot reload). Tailwind + Shadcn UI (`src/components/ui/`) + Context API for state + SSE for real-time streaming. Package manager: **yarn only**.

## PAGES (37 total — `src/pages/`)
- **Public/Marketing**: `Landing`, `Pricing`, `VsDevin`, `PolicyPage`, `BugHunt` (has custom `bug-hunt-bg.png` background, scoped to this page only)
- **Auth**: `Login`, `Signup`, `OAuthFinish`
- **App Core**: `Dashboard`, `DashboardPreviewV2`, `Projects`, `CodebaseHealth`, `ToolsPage`, `Settings`, `Integrations`, `Tokens`, `Domain`, `Deploy`, `Automations`, `Analytics`, `BrainDump`, `FeatureWindow`, `OpsRecipes`, `ShipWall`, `Wrapped`, `SystemStatsPage`, `SidebarPreview`
- **Admin Suite**: `Admin`, `AdminOverview`, `AdminFinancials`, `AdminVanguard`, `AdminLLMCredits`, `AdminParliamentLive`, `AdminFeatureFlags`, `AdminBINTracker`, `AdminApiKeys`, `AdminIntegrations`

## KEY COMPONENTS (`src/components/`)
| Component | Purpose |
|---|---|
| `ChatPanel` / `ChatPanelF12` / `MessageBubble` / `CodeBlock` | AI CTO chat with SSE streaming |
| `FixProgressDrawer` + `BulkFixConfirmModal` + `FixJobContext` | Live fix-pipeline UI (SSE) |
| `SecurityScanDrawer` | Security scan results drawer |
| `KnowledgeGraph` / `GraphPanel` / `MermaidBlock` | Repo visualization |
| `NewUserWizard` + `AddProjectWizard` | Onboarding flow |
| `dashboard/v2/` — `SidebarBound`, `TopBar`, `DeveloperSidebar` | Dashboard v2 shell; DeveloperSidebar is an accordion with the 4 scan tools |
| `LoopModeToggle` / `LoopStepBar` / `LoopActionCards` | Autonomous loop mode UI |
| `AdminRobotGuide` / `AuremAdminPanel` / `AdminHouseRules` | Admin-editable content panels |
| `PatRequiredCTA` / `ConnectRepoBanner` / `GitHubCard` | GitHub PAT/repo connection prompts |
| `FounderOfferCard` / `FounderOfferPill` | Founder-offer monetization surfaces |

## UI CONVENTIONS
- Avatar dropdown contains ONLY Settings and Logout.
- Preview/Graph tabs and "New Run" button are hidden when no repo is connected.
- Every interactive/user-facing element must carry a unique kebab-case `data-testid`.
- Text hierarchy: H1 `text-4xl sm:text-5xl lg:text-6xl`, H2 max `text-lg`, body `text-base`.

## RULES FOR THE AI DEVELOPER (hard constraints)
1. All frontend↔backend traffic goes through `${process.env.REACT_APP_BACKEND_URL}/api/*` — NEVER call GitHub, LLM providers, or any external service directly from the frontend.
2. Streaming UI (chat, fix progress) MUST consume SSE — no polling, no websockets. Match the existing `ChatPanel` / `FixProgressDrawer` pattern.
3. New admin pages go under the existing Admin suite (`Admin*.jsx` naming + existing admin routing) — never a parallel admin area.
4. Component naming keeps the `[Feature][Type]` convention (`BulkFixConfirmModal`, `LoopStepBar`).
5. Onboarding UI extends `NewUserWizard` / `AddProjectWizard` — no separate onboarding flow.
6. Never render, store, or log `github_token` or any credential in frontend state.
7. Page-scoped styling (like the BugHunt background) must stay scoped to its page — never leak into global CSS.
8. Dependencies via `yarn add` only (npm breaks the lockfile).
